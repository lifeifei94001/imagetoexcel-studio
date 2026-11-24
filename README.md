# Image to Excel Studio

基于 **Vue 3 + Element Plus + FastAPI + SQLModel (SQLite WAL / Turso 双模) + Gemini Vision + openpyxl** 构建的「AI 图片转可编辑 Excel 表格」工作台。

## 本地启动

### 1. 启动后端
```powershell
cd backend
uvicorn main:app --reload --port 8000
```
（如需启用真实多模态识别，可在项目根目录创建 `.env` 并填入 `GEMINI_API_KEY=...`；未配置时自动以演示结构化提取模式运行，仍可完整体验编辑与 `.xlsx`/`.csv` 导出。）

### 2. 启动前端
```powershell
cd frontend
npm install
npm run dev
```

## 云端部署（Vercel + Turso）
1. 在 Vercel 导入仓库或使用 `deploy-to-vercel` 部署；
2. 环境变量按需配置 `GEMINI_API_KEY`、`TURSO_DATABASE_URL`、`TURSO_AUTH_TOKEN`。
