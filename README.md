# Paper Reading Collector

根据输入主题在 Google Scholar 搜索论文，并实时写出整理文档。

## 功能

- 输入英文主题进行搜索
- 获取并整理论文：
  - 网址
  - 标题
  - 作者
  - 期刊/会议与年份
  - Abstract
  - Discussion（若不可得则标注）
- 每次运行输出到独立目录，目录名为：运行时间 + 搜索主题
- 实时输出日志与实时写文件（边搜边写）
- 同主题增量缓存，已整理过的论文下次不重复整理
- 同时输出中文翻译文件（与英文文件同目录）

## 安装

```bash
cd paper_reading
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 运行

```bash
python main.py --topic "large language model" --max-results 20
```

遇到 Google Scholar 拉取失败时可以这样运行：

```bash
python main.py --topic "pangenome" --max-results 5 --retries 5 --retry-wait 8 --proxy-mode free
```

参数：

- `--topic`: 英文主题（必填）
- `--max-results`: 目标新增论文数（缓存跳过不计入），默认 `20`
- `--retries`: 启动 Google Scholar 检索失败时重试次数，默认 `3`
- `--retry-wait`: 每次重试等待秒数，默认 `5`
- `--proxy-mode`: 代理模式，`none` 或 `free`，默认 `none`
- `--max-scan-results`: 最大扫描结果数，默认 `500`（防止缓存太多时无限扫描）

## 常见报错

报错：`Cannot Fetch from Google Scholar`

常见原因：

- Google Scholar 临时限流/验证码
- 网络到 Scholar 不稳定
- 请求频率过高

建议：

- 降低 `--max-results`（例如 5）
- 增加 `--retries` 和 `--retry-wait`
- 启用 `--proxy-mode free`

## 输出结构

- `outputs/<YYYYMMDD_HHMMSS>_<topic>/papers.md`
- `outputs/<YYYYMMDD_HHMMSS>_<topic>/papers_zh.md`
- `cache/<topic>.json`

<!-- python3 main.py --topic pangenome --proxy-mode free --max-results 30 -->