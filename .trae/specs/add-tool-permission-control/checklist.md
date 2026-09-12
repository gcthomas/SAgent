# Checklist

- [x] PermissionPolicy 三态决策（allow/ask/deny）实现符合 spec——只读放行、危险 ask、极度危险 deny、用户规则覆盖、last-match-wins
- [x] DANGEROUS_SHELL_PATTERNS 覆盖跨平台（Linux shell 与 Windows PowerShell）危险命令（如 rm -rf /、git push -f、curl|sh、del /s /q、Format-Volume、iex、net user /add 等），忽略大小写
- [x] InteractiveApprovalHandler 交互 y/n/a 与会话级 always-allow 记忆实现符合 spec
- [x] 审批超时 / EOF / 非交互终端默认拒绝（fail-safe）实现符合 spec
- [x] PermissionEnforcer.authorize 三态流程（allow 放行、deny 拒绝 message、ask 经审批批准/拒绝）实现符合 spec
- [x] ToolRegistry.execute 在参数校验后、执行前调用 permission.authorize，被拒返回"错误: 工具 'xxx' 未获用户批准"字符串；permission=None 时保持现有行为
- [x] 引擎（ReActEngine / PlanEngine）不改动——拒绝信息经 execute 返回值天然作为 tool 观察回写给 LLM，无重复授权/重复弹窗
- [x] config 新增 permissions 段（enabled / ask_timeout（缺省 600 秒）/ non_interactive / allow / deny / ask），与 config.models、config.example.yaml、config.yaml 对齐
- [x] CLI 组装层在 permissions.enabled 时注入 PermissionEnforcer，启动横幅打印权限状态
- [x] 权限决策记录 event="permission_decision" 审计日志（含 decision/tool/tool_args/reason，拒绝或超时 WARNING、放行 INFO）
- [x] 向后兼容：ToolRegistry 与 build_default_registry 的 permission 参数缺省 None，现有测试（直接构造注册表/引擎触发工具）行为不变
- [x] 新增单元测试（policy / approval / enforcer / registry 拦截 / CLI 注入）覆盖上述行为
- [x] `python -m pytest` 全部用例通过，无回归
