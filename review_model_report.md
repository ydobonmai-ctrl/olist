# 리뷰 불만 유형 분류 모델 평가

- 정답 데이터: 무작위 표본 1,500건 (LLM 직접 라벨링)
- 모델: TF-IDF(단어 1~2gram + 글자 3~5gram) + 로지스틱 회귀
- 5-fold 층화 교차 검증: **정확도 0.751, macro F1 0.703**
- 적용 대상: 불만 리뷰 14,377건 (표본 1,500건은 정답 라벨 사용)

## 유형별 성능

| 유형 | 정밀도 | 재현율 | F1 | 표본 수 |
|---|---:|---:|---:|---:|
| delivery_delay | 0.761 | 0.744 | 0.753 | 90 |
| not_received | 0.818 | 0.857 | 0.837 | 405 |
| damaged_defective | 0.766 | 0.667 | 0.713 | 123 |
| wrong_or_missing_item | 0.807 | 0.813 | 0.810 | 359 |
| quality_mismatch | 0.728 | 0.752 | 0.740 | 210 |
| seller_service | 0.620 | 0.537 | 0.575 | 82 |
| positive_or_neutral | 0.752 | 0.794 | 0.773 | 107 |
| other | 0.432 | 0.411 | 0.421 | 124 |

## 혼동 행렬 (행: 정답, 열: 예측)

```
                       delivery_delay  not_received  damaged_defective  wrong_or_missing_item  quality_mismatch  seller_service  positive_or_neutral  other
delivery_delay                     67            10                  0                      1                 0               0                    4      8
not_received                        8           347                  1                     17                 0               8                    3     21
damaged_defective                   2             5                 82                     11                15               3                    3      2
wrong_or_missing_item               3            18                  8                    292                21               4                    2     11
quality_mismatch                    0             4                 10                     15               158               4                   11      8
seller_service                      2            11                  2                      6                 3              44                    1     13
positive_or_neutral                 3             4                  0                      3                 8               0                   85      4
other                               3            25                  4                     17                12               8                    4     51
```
