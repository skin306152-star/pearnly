# 识别用的本地模型

## doc_ori.onnx —— 文档方向分类（0° / 90° / 180° / 270°）

- 来源：PaddlePaddle 官方 `PP-LCNet_x1_0_doc_ori`（https://huggingface.co/PaddlePaddle/PP-LCNet_x1_0_doc_ori），
  许可证 **Apache-2.0**；官方报告准确率 99.06%。
- 格式：官方只发 Paddle 推理格式，这里用官方转换工具 paddle2onnx 2.1.0 转成 ONNX（opset 13），命令：
  `paddle2onnx --model_dir model --model_filename inference.json --params_filename inference.pdiparams --save_file doc_ori.onnx --opset_version 13`
  （在 python:3.12-slim + paddlepaddle 3.0.0 的一次性容器里转，2026-09-27）。
- 原始权重 `inference.pdiparams` sha256 `e8d6e7c5d264507e40e58a655779059d616b20d7441ea22047d829eb3931989c`；
  本文件 sha256 `b7a2b303f8ffc4a269b7f782ae73d526f3739f23cd85f9337c532468d44560a7`。
- 运行：onnxruntime（CPU），输入 1×3×224×224，预处理为短边缩到 224 后沿长边取三块、减均值除方差；输出四个方向的概率。
  用法见 `pearnly_core.ocr.orient`（ADR-013）。

换模型或重新转换时，更新本文件的 sha256 并用 `docs/work/2026-09-27-ocr-corpus-eval.md` 第 9 节的方法重测。
