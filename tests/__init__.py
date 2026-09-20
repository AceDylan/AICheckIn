# -*- coding: utf-8 -*-
"""测试包。整套一起跑：仓库根目录执行 `python -m unittest discover -s tests -t .`。

这里统一准备 app.py 在「导入时」就要读的环境变量——包 __init__ 必然先于任何
tests.test_* 子模块执行，因此各测试模块不必（也不应）再各自设置一遍。
每个用例真正使用的数据目录由 tests._support.StoreIsolationMixin 再行重定向。
"""
import os
import tempfile

os.environ["GYQD_SCHEDULER"] = "0"                      # 不启动后台定时线程
os.environ["GYQD_ADMIN_PASSWORD"] = ""                  # 宿主机若设了密码，测试一律按未设处理
os.environ.setdefault("GYQD_CONFIG_FILE", os.path.join(tempfile.mkdtemp(), "config.json"))

import app as _app_module  # noqa: E402  必须排在上面几行环境变量之后
from flask.testing import FlaskClient  # noqa: E402


class _ClosingClient(FlaskClient):
    """静态文件的响应体是一个开着的文件句柄：用例里 `client.get("/static/…").get_data()` 读完就丢，
    句柄要等垃圾回收才关，整套跑下来是二十几条 ResourceWarning，把真正该看的告警淹掉。
    buffered=True 让 werkzeug 把响应体读完当场关闭；对用例拿到的状态码、头和内容没有影响。"""

    def open(self, *args, **kwargs):
        kwargs.setdefault("buffered", True)
        return super().open(*args, **kwargs)


_app_module.app.test_client_class = _ClosingClient
