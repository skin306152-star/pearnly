"""甲骨文 free-arm 上的一个进程跑两件事（ADR-022 / ADR-023）：

- 8443（只放行生产 IP、自签证书钉死指纹、带令牌）：Claude 识别 ``/v1/read``，加上 Cowork 原件的内部接口
  ``/v1/originals/*``（上传后核对、删除、取回）；
- 本机端口 ``ORIGINALS_PUBLIC_PORT``（默认 8444，只听 127.0.0.1）：原件的签名地址上传 / 预览 / 下载，经 Cloudflare 隧道接
  ``originals.pearnly.com``，这台不对外开端口。

共用层的识别服务不导入产品代码：两边在这里拼起来。
"""
import os
import threading

from pearnly_core.claude_ocr import server as claude
from pearnly_cowork.files import edge_server


class Handler(claude.Handler):
    def route(self, path, body):
        return edge_server.route(path, body)


def main():
    # 容器里听全部地址，宿主机只把它映射到 127.0.0.1（deploy.sh 的 -p 127.0.0.1:8444:8444）：只有本机的隧道进得来。
    public = edge_server.public_server(os.environ.get("ORIGINALS_PUBLIC_PORT", "8444"), os.environ["ORIGINALS_SIGNING_KEY"],
                                       host="0.0.0.0")
    threading.Thread(target=public.serve_forever, name="originals-public", daemon=True).start()
    claude.main(Handler)


if __name__ == "__main__":
    main()
