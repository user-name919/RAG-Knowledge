# 文件上传与 Vue Demo 验证（2026-09-29）

## 已执行

- Python 自动化测试：28 项通过，包含原 QA 回归、上传类型/大小/编码校验、路径穿越防护、访问范围、只读权限、版本替换、删除、并发替换、PDF 页码、扫描件失败及任务重试。
- 原 QA 的真实 MySQL/ES 集成测试通过：并发领取、幂等、融合评分、过滤、权限、更新和删除（该项使用测试向量）。
- Vue 3 / Vite：生产构建成功，Docker 镜像内包含静态资源，首页与资源通过 HTTP 可访问。
- 本机 Docker：FastAPI、Worker、MySQL、ES 联通；已有 QA 数据保留。新增文件表和任务表，ES 仅补充字段。
- 使用虚构 Markdown、TXT、文本型 PDF，经真实 SiliconFlow Embedding、Worker 和 ES 完成上传、索引、召回及原文件下载。实际结果见 `VERIFICATION_DOCUMENTS_RESULTS.json`。
- Markdown 返回章节位置；PDF 返回第 1 页；替换后仅召回 v2；删除后不再召回；空白/扫描 PDF 显式报 OCR 提示；只读 Key 上传被拒绝、下载授权文件正常。

## 验证边界

- 这些结果验证功能链路，不代表真实业务问答准确率；用户上传实际项目资料后还需要构建评测集。
- 内置浏览器拒绝访问 localhost，本机浏览器自动化未取得系统权限，因此未完成浏览器点击上传和视觉验收；前端生产构建与 API 链路已验证。
- 暂不包含 OCR、Word/Excel 解析和答案生成。
