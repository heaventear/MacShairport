"""Lightweight i18n for terminal reports (中文 / English).

Keeps the reporting layer language-agnostic: report builders look up display
strings by key via :func:`tr`, so the same numbers render in either language.
Only chrome (labels, headings, notes) is translated — data values (market ids,
topics, YES/NO, status codes) stay canonical, matching the web dashboard.
"""

from __future__ import annotations

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        # daily report
        "day_report_title": "=== Day {day} Report ===",
        "lbl_equity": "Equity",
        "lbl_cash": "Cash",
        "lbl_realized": "Realized PnL",
        "lbl_unrealized": "Unrealized PnL",
        "lbl_open_positions": "Open positions",
        "lbl_drawdown": "Drawdown",
        "lbl_risk_state": "Risk state",
        # final report
        "final_title": "{days}-Day Experiment Report (SIMULATION)",
        "sec_profit": "Profit",
        "sec_trading": "Trading",
        "sec_ai": "AI quality",
        "sec_breakdowns": "Breakdowns",
        "sec_engineering": "Engineering",
        "sec_success": "Success criteria",
        "p_start": "Start equity",
        "p_end": "End equity",
        "p_net": "Net PnL",
        "p_maxdd": "Max drawdown",
        "p_fees": "Total fees",
        "p_slip": "Slippage cost",
        "tr_total": "Total trades",
        "tr_wl": "Win / Loss",
        "tr_winrate": "Win rate",
        "tr_avg": "Avg win / loss",
        "tr_anom": "Anomalous trades",
        "ai_brier": "Brier score",
        "ai_brier_note": "(0=perfect, 0.25=always-0.5)",
        "ai_calib": "Calibration",
        "bd_topic": "By topic",
        "bd_conf": "By confidence",
        "bd_evidence": "By evidence",
        "bd_line": "trades={count} settled={settled} win={wr} net=${net}",
        "eng_api": "API errors",
        "eng_risk": "Risk rejections",
        "eng_dupe": "Duplicate suppressed",
        "eng_unknown": "UNKNOWN orders",
        "eng_cancel": "Cancellations",
        "eng_pauses": "System pauses",
        "eng_reconciled": "Ledger reconciled",
        "overall": "OVERALL: {result}",
        "pass_word": "PASS",
        "fail_word": "FAIL",
        "closing1": "NOTE: a 7-day simulation result does not prove long-term",
        "closing2": "profitability (spec 十三/十四). Treat as a systems test only.",
        # success criteria
        "crit_no_drawdown_breach": "no drawdown-limit breach",
        "crit_no_duplicate_orders": "no duplicate orders",
        "crit_no_anomalous_fills": "no unexplained anomalous fills",
        "crit_ledger_reconciled": "ledger reconciled",
        "crit_all_traceable": "all trades traceable",
        "crit_risk_active": "risk manager active",
        # runner messages
        "run_live_notice": "Running against LIVE read-only Polymarket data (no orders placed).",
        "refuse_sim": "REFUSING TO RUN: execution.mode must be 'simulation' in this phase.",
    },
    "zh": {
        "day_report_title": "=== 第 {day} 天报告 ===",
        "lbl_equity": "权益",
        "lbl_cash": "现金",
        "lbl_realized": "已实现盈亏",
        "lbl_unrealized": "未实现盈亏",
        "lbl_open_positions": "持仓数",
        "lbl_drawdown": "回撤",
        "lbl_risk_state": "风控状态",
        "final_title": "{days} 天实验报告（模拟）",
        "sec_profit": "收益指标",
        "sec_trading": "交易指标",
        "sec_ai": "AI 指标",
        "sec_breakdowns": "分项表现",
        "sec_engineering": "工程指标",
        "sec_success": "实验成功标准",
        "p_start": "初始权益",
        "p_end": "期末权益",
        "p_net": "净盈亏",
        "p_maxdd": "最大回撤",
        "p_fees": "手续费合计",
        "p_slip": "滑点成本",
        "tr_total": "总成交笔数",
        "tr_wl": "胜 / 负",
        "tr_winrate": "胜率",
        "tr_avg": "平均盈利 / 亏损",
        "tr_anom": "异常成交",
        "ai_brier": "Brier 分数",
        "ai_brier_note": "（0=完美，0.25=恒为0.5）",
        "ai_calib": "校准度",
        "bd_topic": "按主题",
        "bd_conf": "按置信度",
        "bd_evidence": "按证据",
        "bd_line": "笔数={count} 已结算={settled} 胜率={wr} 净盈亏=${net}",
        "eng_api": "API 异常",
        "eng_risk": "风控拒绝",
        "eng_dupe": "重复订单拦截",
        "eng_unknown": "UNKNOWN 订单",
        "eng_cancel": "撤单次数",
        "eng_pauses": "系统暂停次数",
        "eng_reconciled": "账本对账",
        "overall": "总体：{result}",
        "pass_word": "通过",
        "fail_word": "未通过",
        "closing1": "注意：七天模拟结果不能证明长期盈利能力",
        "closing2": "（见规格 十三/十四）。仅作为系统测试。",
        "crit_no_drawdown_breach": "未突破最大回撤限制",
        "crit_no_duplicate_orders": "无重复下单",
        "crit_no_anomalous_fills": "无无法解释的异常成交",
        "crit_ledger_reconciled": "账实一致",
        "crit_all_traceable": "所有交易可追溯",
        "crit_risk_active": "风控模块有效",
        "run_live_notice": "正在使用 Polymarket 实时只读数据运行（不下真实订单）。",
        "refuse_sim": "拒绝运行：本阶段 execution.mode 必须为 'simulation'。",
    },
}


def normalize_lang(lang: str | None) -> str:
    """Map any input to a supported language code, defaulting to English."""
    if not lang:
        return "en"
    lang = lang.lower()
    return "zh" if lang.startswith("zh") else "en"


def tr(lang: str, key: str, **kwargs) -> str:
    """Look up a translated string, falling back to English then the key."""
    lang = normalize_lang(lang)
    table = STRINGS.get(lang, STRINGS["en"])
    s = table.get(key)
    if s is None:
        s = STRINGS["en"].get(key, key)
    return s.format(**kwargs) if kwargs else s
