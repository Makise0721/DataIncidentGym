# P1 Benchmark Report

- Manifest: `p1-formal-v9`
- Manifest SHA-256: `698752e82f3a1865bfc25bd41a52a0f77210529ce50a94c76c944d7e5393cfba`
- Implementation revision: `0c3cc61489e7a3c59023090a810981ea82b3662f`
- Checkout revision: `493f4af384392c0c4a0843c0ecba4b9d999152e8`

## Conclusion

正式样本存在环境或安全硬门失败，结果无效。

## Coverage

| Strategy | Cells | Completed | Failed |
|---|---:|---:|---:|
| STATIC_SKILL | 36 | 4 | 32 |
| DIAGNOSTIC_KERNEL | 36 | 10 | 26 |
| NO_TOOL | 12 | 0 | 12 |
| KERNEL_NO_LINEAGE | 5 | 0 | 5 |
| KERNEL_NO_SCHEMA | 5 | 0 | 5 |
| FIXED_RULE | 12 | 11 | 1 |

## Main metrics

| Strategy | Paired success | Root cause | Unsupported confirmation | Status | Claim evidence | No incident | Assets macro-F1 |
|---|---|---|---|---|---|---|---:|
| STATIC_SKILL | 1/15 (0.067; 95% CI 0.012-0.298) | 10/15 (0.667; 95% CI 0.417-0.848) | 0/15 (0.000; 95% CI 0.000-0.204) | 24/36 (0.667; 95% CI 0.503-0.798) | 8/37 (0.216; 95% CI 0.114-0.372) | 2/6 (0.333; 95% CI 0.097-0.700) | 0.798 |
| DIAGNOSTIC_KERNEL | 0/15 (0.000; 95% CI 0.000-0.204) | 4/15 (0.267; 95% CI 0.109-0.520) | 2/15 (0.133; 95% CI 0.037-0.379) | 12/36 (0.333; 95% CI 0.202-0.497) | 18/18 (1.000; 95% CI 0.824-1.000) | 6/6 (1.000; 95% CI 0.610-1.000) | 0.185 |

## Main-strategy efficiency

Only evaluator-passing cells contribute tool/token/request/elapsed summaries; failure rates retain the full strategy denominator.

| Strategy | Successful tools median | Exact duplicates | Equivalent calls | Post-decisive median | Model errors | Timeouts | Requests total |
|---|---:|---:|---:|---:|---|---|---:|
| STATIC_SKILL | 3.0 | 3 | 0 | 2.0 | 11/36 (0.306; 95% CI 0.180-0.469) | 2/36 (0.056; 95% CI 0.015-0.181) | 16 |
| DIAGNOSTIC_KERNEL | 3.0 | 3 | 0 | 0 | 20/36 (0.556; 95% CI 0.396-0.705) | 1/36 (0.028; 95% CI 0.005-0.142) | 41 |

### Failure and usage details

| Strategy | Request-limit exhaustion | Tool-limit exhaustion | Input tokens | Output tokens | Elapsed median ms |
|---|---|---|---:|---:|---:|
| STATIC_SKILL | 2/36 (0.056; 95% CI 0.015-0.181) | 2/36 (0.056; 95% CI 0.015-0.181) | 98137 | 18725 | 102415.0 |
| DIAGNOSTIC_KERNEL | 2/36 (0.056; 95% CI 0.015-0.181) | 0/36 (0.000; 95% CI 0.000-0.096) | 408120 | 54537 | 108180.0 |

## Hard gates

| Gate | Passed |
|---|---|
| doctor_passed | yes |
| ledger_complete | yes |
| artifacts_complete | yes |
| identity_aligned | yes |
| kernel_state_valid | yes |
| fixed_rule_zero_model_usage | yes |
| evaluator_safety | no |

### Invalid gates

- `8fb1f2190e7d48d28cf3cb168b67c840`: `ENVIRONMENT_VERIFIED` (`ENVIRONMENT_VERIFIED_FAILED`)

## Auxiliary policies

No Tool、Kernel 消融和 Fixed Rule 单独列示，不纳入 Static/Kernel 优势判定。

| Policy | Cells | Completed | Failed |
|---|---:|---:|---:|
| NO_TOOL | 12 | 0 | 12 |
| KERNEL_NO_LINEAGE | 5 | 0 | 5 |
| KERNEL_NO_SCHEMA | 5 | 0 | 5 |
| FIXED_RULE | 12 | 11 | 1 |
