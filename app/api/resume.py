"""简历上传：PDF → 纯文本 → 写入该会话的 state。

为什么写进 state 而不是直接拼进聊天记录：
- 简历原文属于「事实材料」，不该混在对话历史里（会污染上下文、被摘要压缩掉）
- 放在 state 的独立字段，每次优化都能稳定读到完整原文
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app import auth
from app.api.chat import get_graph
from app.parsing import pdf_to_text

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
