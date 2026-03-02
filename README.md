# Paper Summarizer Plugin

AstrBot 插件：上传 PDF 自动中文总结 Abstract。

## 功能

- /paper 指令触发
- 60 秒内上传 PDF
- 自动提取 Abstract
- 调用 Ollama 模型中文总结
- 自动删除文件
- 严格 session 隔离

## 依赖

- AstrBot >= 4.9.2
- Ollama
- pdfplumber
- requests

## 环境变量

OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=gemma3:27b

## 文件存储位置

data/plugin_data/paper_summarizer/

处理完成后自动删除。

## 使用方法

/paper  
上传 PDF 文件

## 说明

- 仅支持包含 "Abstract" 字样的论文
- 默认只扫描前 3 页
- 中间处理状态不对用户显示
