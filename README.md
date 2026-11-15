# rpa-form

表单批量录入骨架，配一个本地模拟站点 `rpa/mock_server.py`。

## 已知缺口

- 不取 CSRF token，提交全部 403
- 不保持 cookie 会话
- 不去重，B001 会提交两次并撞 409
- 不做本地校验，空名字和超长字段直接发给服务端
- 不重试、不限速、不落状态

## 运行

```powershell
python gen_samples.py
python -m rpa.mock_server 8099
python -m rpa.cli rows.csv --form f001
python -m pytest tests -q
```
