"""WeChat report delivery (doc/wechat_tasks.md).

Only protocol-independent parts are implemented here. The real iLink Bot
client is an explicit, mockable interface; the concrete implementation is
blocked on protocol verification (see doc/wechat_phase0_survey.md).
"""
