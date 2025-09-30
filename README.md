# Datasheet 校對系統（QA 版，完整專案）

## 功能
- 上傳或以 URL 嘗試下載 PDF（遇反爬請人工下載再上傳）
- 依內容雜湊（SHA-256）去重，避免重複解析與重複計費
- 解析佇列（背景執行），不阻塞人員操作
- 完全沿用你的 JSON schema（**不新增任何鍵**），結果 JSON 落地保存 + 切分至型號項目
- 本機網頁 UI：PDF.js 頁面預覽、全文搜尋、JSON 校對與狀態切換、JSON/CSV 匯出
- SQLite（免安裝伺服器），需要時可升級到 SQLite JSONB（3.45.0+）

## 安裝與啟動
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # 填入 OPENAI_API_KEY
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```
瀏覽器打開：`http://127.0.0.1:8000/`

## 目錄
- `workspace/`：PDF 存放、解析結果、匯出等
- `resources/openai/system_instructions/`：請放入你的 `擷取型號.md`、`擷取規格.md`
- `resources/openai/response_format/`：請放入你的精確 JSON Schema（目前提供 placeholder；若缺失則校驗自動跳過）

## 注意
- 若 PDF 為掃描圖，請先 OCR（例如 ocrmypdf）
- 若 schema 檔未提供，後端會自動以「可選校驗」方式跳過嚴格驗證，避免阻塞人工校對
