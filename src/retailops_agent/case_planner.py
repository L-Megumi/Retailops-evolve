"""Task contract and dependency plan for built-in and evolvable case checks."""
from __future__ import annotations

from dataclasses import dataclass
import re

from .case_capabilities import CAPABILITIES

PATTERNS = {'records': r'记录一致性|重复记录|关联订单', 'amount': r'实付金额|金额核查|超额退款',
            'freight': r'运费|承担方', 'materials': r'材料|凭证缺失|缺少凭证'}
for name, definition in CAPABILITIES.items():
    PATTERNS[name] = '|'.join(re.escape(a) for a in definition.aliases)


@dataclass(frozen=True)
class CasePlan:
    required_checks: tuple[str, ...]
    forbidden_checks: tuple[str, ...]
    steps: tuple[dict, ...]
    source: str
    evolution_allowed: bool = True


class CaseTaskPlanner:
    def plan(self, task, explicit, defaults):
        # Preserve the existing selector's semantics, then add named capabilities.
        from .case_review import CaseReviewAgent
        base = CaseReviewAgent._select_checks(task, explicit, defaults)
        denied = set()
        inferred = []
        only = False
        for clause in re.split(r'[，,；;。]', task):
            names = [n for n,p in PATTERNS.items() if re.search(p,clause,re.I)]
            if re.search(r'(?:不|不要|无需|禁止)(?:再)?(?:检查|核查|查|计算|统计|看)|do not|never',clause,re.I):
                denied.update(names)
            elif names:
                inferred.extend(names)
                only |= bool(re.search(r'(?:只|仅)(?:需|需要)?(?:检查|核查|查|看|计算|统计)|only',clause,re.I))
        if explicit and set(explicit) & denied:
            raise ValueError('explicit checks conflict with task exclusions')
        selected = list(explicit) if explicit else (inferred if only and inferred else base + inferred)
        selected = tuple(dict.fromkeys(n for n in selected if n not in denied))
        if not selected or len(selected)>12:
            raise ValueError('no applicable requested checks or too many checks')
        steps = [{'id':'link_records','depends_on':[]}]
        for check in selected:
            if check in {'freight','materials'}:
                dependencies=['link_records','retrieve_policy']
                if not any(s['id']=='retrieve_policy' for s in steps):
                    steps.append({'id':'retrieve_policy','depends_on':['link_records']})
            else:
                dependencies=['link_records']
            steps.append({'id':'check:'+check,'depends_on':dependencies,
                          'capability_type':'metric_adapter' if check in CAPABILITIES else 'builtin_or_gap'})
        steps.append({'id':'report','depends_on':['check:'+c for c in selected]})
        evolution_allowed = not re.search(r'(?:不|不要|禁止|不允许)(?:自动)?(?:生成|修改|新增)(?:任何)?(?:插件|代码|工具)|禁止演进|do not generate',task,re.I)
        return CasePlan(selected,tuple(sorted(denied)),tuple(steps),'explicit' if explicit else 'task_and_case',evolution_allowed)
