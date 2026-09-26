"""简历相关接口：上传 PDF 解析、把草稿渲染成可打印的 HTML。

为什么简历原文写进 state 而不是直接拼进聊天记录：
- 简历原文属于「事实材料」，不该混在对话历史里（会污染上下文、被摘要压缩掉）
- 放在 state 的独立字段，每次优化都能稳定读到完整原文
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

from app import auth
from app.api.chat import get_graph
from app.graph.agents.resume_builder import missing_labels
from app.parsing import pdf_to_text
from app.resume_render import render

router = APIRouter()

MAX_BYTES = 5 * 1024 * 1024


@router.post("/api/resume/upload")
async def upload_resume(
    file: UploadFile = File(...),
    thread_id: str = Form(...),
    user_id: int = Depends(auth.current_user),
) -> dict:
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="目前只支持 PDF 格式")

    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 5MB")

    text = pdf_to_text(data)
    # 扫描件（图片型 PDF）提取不出文字，这种情况要明确告诉用户，而不是让 Agent 瞎猜
    if len(text.strip()) < 50:
        raise HTTPException(
            status_code=422, detail="解析不出文字内容，可能是扫描件（图片型 PDF）"
        )

    # 写入该会话的 state，后续简历优化 Agent 直接读这里
    get_graph().update_state(
        {"configurable": {"thread_id": f"{user_id}:{thread_id}"}},
        {"resume_text": text},
    )

    return {"chars": len(text), "preview": text[:200]}


@router.get("/api/resume/preview")
def preview_resume(
    thread_id: str, embed: bool = False, user_id: int = Depends(auth.current_user)
) -> HTMLResponse:
    """把该会话的简历草稿渲染成可打印的 HTML。

    三个决定：
    - 用 GET：要能直接在新窗口打开、能刷新，也不该重复提交任何东西。
    - 信息没齐也照样渲染（页顶提示还缺什么）：让人看到半成品，比直接报错有用。
    - embed=true 给前端右侧预览面板用（iframe 内嵌）：去掉「按 Ctrl+P」这类
      操作提示，因为那个场景下用户不是要打印，是在边聊边看。
    """
    state = get_graph().get_state(
        {"configurable": {"thread_id": f"{user_id}:{thread_id}"}}
    )
    draft = (state.values or {}).get("resume_draft") or {}
    return HTMLResponse(render(draft, missing_labels(draft), embed=embed))
