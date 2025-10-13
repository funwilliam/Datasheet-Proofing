# Datasheet Proofing — 專案導覽（Proposed README）

> 本文件為依照程式碼實際行為整理的最新專案導覽，涵蓋所有頁面路徑與 API 端點、參數與注意事項。

---

## Pages（前端頁面）

| 路徑 | 用途 | 主要功能 |
|---|---|---|
| `/` | 入口 | 307 redirect -> `/files` |
| `/files` | 檔案管理 | 列表 + 分頁、上傳 PDF（多檔）、批次貼 URL 入列（下載）、觸發擷取任務、快速連結至檔案詳情與校對 |
| `/files/{file_hash}` | 檔案詳情 | 檔案中繼資料（hash、檔名、建立時間、是否已解析）、快捷操作（開啟 PDF、整份校對）、關聯型號列表、解除檔案與型號關聯 |
| `/review/{file_hash}` | 校對頁 | 左側 PDF.js 預覽 + 全文搜尋；右側規格欄位編輯（雙擊儲存格進入編輯、Enter 儲存、Esc 取消）；欄位 hover 顯示 AI 擷取依據；整份/逐欄位校對與驗證 |
| `/models` | 型號管理 | 型號清單（搜尋、分頁、是否已驗證/是否有檔案過濾）、規格參數編輯 Modal、標記已驗證（記錄 reviewer 與 reviewed_at）|
| `/tasks` | 佇列監控 | 下載任務與擷取任務的即時列表、依狀態過濾、重試下載（失敗項）|
| `/pdf/{file_hash}` | PDF 直出 | 直接回傳 PDF 檔（二進位），可被瀏覽器或外部工具開啟 |

---

## API（後端端點）

### 1) Files — `/api/files`

- `GET /api/files`  
  **用途**：檔案列表（分頁）。  
  **Query**：
  - `page` (int, ≥1, default 1)
  - `page_size` (int, 1~500, default 50)  
  **Response**：`{ items: [...], total, page, page_size }`；每個 item 含 `file_hash, filename, source_url, size_bytes, local_path, created_at, parsed`。

- `GET /api/files/{file_hash}`  
  **用途**：單檔資訊。  
  **Response**：`FileAssetOut` + `parsed`（布林；是否已有 `/workspace/extractions/{file_hash}.json`）。

- `POST /api/files/upload-multi` (multipart/form-data)  
  **用途**：多檔上傳。欄位：`files`（可多個）。  
  **Response**：`{ uploaded, items: [{ file_hash, filename }, ...] }`。

- `POST /api/files/upload-urls` (form-data)  
  **用途**：貼上多個下載 URL，轉呼叫 `/api/downloads/enqueue` 入列。  
  **Body**：
  - `urls`（多筆；每列一個 URL）
  - `hsd_name`（可選）  
  **Response**：`{ queued, task_ids: [...] }`。

- `GET /api/files/{file_hash}/models`  
  **用途**：列出該檔案關聯的型號（含每個型號曾出現過的所有檔案）。  
  **Response**：`ModelItemOut[]`（精簡版，含 `files: [{file_hash, filename}]`）。

- `DELETE /api/files/{file_hash}/models/{model_number}`  
  **用途**：解除檔案與型號的關聯。  
  **注意**：程式碼中**沒有**對應的「建立關聯」API；連結由擷取流程或後台維護（PATCH /api/models/{model_number}）處理。

---

### 2) Downloads — `/api/downloads`

- `POST /api/downloads/enqueue`  
  **用途**：批次將 URL 入列為下載任務。  
  **Body**：`urls: string[]`，`hsd_name?: string`。  
  **行為**：建立 `DownloadTask(status='queued')` 並交由 downloader worker 執行。  
  **Response**：`{ queued, task_ids: [...] }`。

- `GET /api/downloads`  
  **用途**：下載任務列表。  
  **Query**：
  - `limit` (int, 1~1000, default 200)
  - `status`（可選，`queued/running/success/failed`）  
  **Response**：陣列；每列含 `id, source_url, hsd_name, status, file_hash, error, created_at, started_at, completed_at`。

- `POST /api/downloads/{task_id}/retry`  
  **用途**：重試指定下載任務（把狀態重置為 `queued` 並重新入列）。  
  **Response**：`{ ok: true }`。

---

### 3) Tasks（擷取佇列）— `/api/tasks`

- `POST /api/tasks/queue`  
  **用途**：將 `file_hashes` 批次入列到擷取佇列。  
  **Body**：`{ file_hashes: string[], force_rerun?: boolean }`。  
  **行為**：
  - 會去重；
  - 若 `force_rerun=false` 且已存在 `extractions/{file_hash}.json` 則跳過；
  - 其餘丟給 extractor worker。  
  **Response**：
  ```json
  {
    "queued": <int>, "skipped_existing": <int>, "not_found": <int>,
    "duplicates_ignored": <int>, "total_input": <int>,
    "queued_hashes": [...], "skipped_hashes": [...], "not_found_hashes": [...]
  }
  ```

- `GET /api/tasks/extraction`  
  **用途**：擷取任務列表。  
  **Query**：
  - `limit` (int, 1~1000, default 200)
  - `status`（可選，`queued/submitted/running/succeeded/failed/canceled`）
  - `mode`（可選，`sync/batch/background`）  
  **Response**：陣列；每列含 `id, file_hash, mode, provider, openai_model, service_tier, status, prompt_tokens, completion_tokens, input_tokens, output_tokens, request_payload_path, response_path, error, created_at, submitted_at, started_at, completed_at`。

- `GET /api/tasks/download`  
  **用途**：下載任務列表（與 `/api/downloads` 類似，主要供 `/tasks` 頁面使用）。  
  **Query**：同上 `limit`、`status`。

---

### 4) Extractions — `/api/extractions`

- `GET /api/extractions/{file_hash}`  
  **用途**：取得某檔案最近的一筆擷取總結與該檔案的型號項目。  
  **Response**：
  ```json
  {
    "extraction": { /* ExtractionTaskOut */ },
    "models": [{
      "id", "file_hash", "model_number", "fields_json", "verify_status",
      "reviewer", "reviewed_at", "notes"
    }]
  }
  ```

- `GET /api/extractions/{task_id}/output`  
  **用途**：以 `task_id` 下載該次擷取輸出的 JSON 檔。  
  **注意**：後端包含路徑白名單檢查，僅允許工作區 `extractions/` 內的檔案。

---

### 5) Models — `/api/models`

- `GET /api/models`  
  **用途**：型號列表。  
  **Query**：
  - `q`（可選；以 `model_number` LIKE 模糊搜尋）
  - `status`（可選；`verified`／`unverified`）
  - `has_files`（可選；`true/false`）
  - `page`（default 1）, `page_size`（1~200, default 50）  
  **Response**：`{ items, total, page, page_size }`，每個 item 含 `model_number` 與關聯檔案清單。

- `GET /api/models/{model_number}`  
  **用途**：單一型號詳情。  
  **Response**：規格欄位（`input_voltage_range, output_voltage, output_power, package, isolation, insulation, applications[], dimension`）、驗證資訊（`verify_status, reviewer, reviewed_at`）、`notes`、以及 `files: [{file_hash, filename}]`。

- `PATCH /api/models/{model_number}`  
  **用途**：更新型號資訊（部分欄位）。  
  **Body**（任選欄位）：上述規格欄位 + `verify_status`（`verified/unverified`）、`reviewer`、`notes`。  
  **驗證規則**：若修改任何規格欄位且原本為 `verified`，會自動改為 `unverified` 並清空 `reviewer / reviewed_at`；若 PATCH 指定 `verify_status=verified`，則會寫入 `reviewer` 與 `reviewed_at`（由伺服器端設定為現在時間）。

- `DELETE /api/models/{model_number}`  
  **用途**：刪除整個型號（連同 applications 與 file 關聯，靠外鍵級聯）。

---

### 6) Export — `/api/export`

- `GET /api/export`  
  **用途**：全庫匯出。  
  **Query**：
  - `status`（可選；例：`verified` 或 `unverified`）
  - `fmt`（預設 `json`，可為 `json` 或 `csv`）  
  **備註**：`fmt=csv` 會以串流下載；固定檔名 `models_export.csv`。

- `POST /api/export/by-models`  
  **用途**：指定型號清單匯出（可非常長）。  
  **Body**：
  ```json
  { "model_numbers": ["ABC-123", "..."], "status": "verified", "fmt": "json|csv", "preserve_order": false }
  ```
  **備註**：`preserve_order=true` 時輸出順序與 `model_numbers` 相同；空清單時 `json` 回空陣列、`csv` 回空檔。

---

### 7) Static Proxy — `/api/static`

- `GET /api/static?path=...`  
  **用途**：以白名單代理方式回傳 JSON 檔案。  
  **Query**：`path`（工作區**絕對路徑**或 repo-root 相對路徑）。  
  **安全限制**：僅允許 `workspace/extractions/` 與 `workspace/exports/` 之下的檔案，其他路徑一律拒絕。

---

## 注意事項（跨端點）
- `status` 字串值請依端點註解；下載任務使用 `queued/running/success/failed`，擷取任務則為 `queued/submitted/running/succeeded/failed/canceled`。
- 所有時間欄位以 ISO 8601 字串回傳（UTC）。
- `applications` 欄位在前端編輯時為「每行一個」；後端以陣列儲存。
- PDF 檔案二進位透過 `GET /pdf/{file_hash}` 直接回應；若需 JSON 結果，使用 `/api/extractions/{task_id}/output` 或 `/api/static?path=...`。

---

## 與舊版 README 的差異 / 更新點（一覽）
- 上傳端點改名：`POST /api/files/upload-multi`、`POST /api/files/upload-urls`（舊文檔出現 `/upload`、`/urls` 的描述已過期）。
- `POST /api/files/{file_hash}/models/{model_number}` **不存在**（舊文檔提及建立關聯的端點）；現行僅提供 `DELETE` 解除關聯；建立關聯由擷取/資料處理流程負責或透過更新模型資訊完成。
- 已找不到 `GET /api/files/{file_hash}/search` 端點（舊文檔列舉）；現行全文搜尋在校對頁由 PDF.js 客端完成。
- 新增或明確化的端點：
  - 匯出：`GET /api/export` 與 `POST /api/export/by-models`（支援超長清單與 `preserve_order`）。
  - 擷取：`GET /api/extractions/{task_id}/output` 可直接下載該次擷取的 JSON。 
  - 靜態代理：`GET /api/static?path=` 僅允許 `extractions/`、`exports/` 路徑。
- 頁面導覽更新：`/files`、`/models`、`/tasks`、`/review/{file_hash}` 與 `/pdf/{file_hash}` 的實作已對齊目前模板內容與腳本。
