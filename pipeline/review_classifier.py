"""불만 리뷰 유형 분류: 정답 라벨 1,500건으로 TF-IDF + 로지스틱 회귀를 학습해 불만 리뷰 전체에 적용.

    python pipeline/review_classifier.py

- 정답 라벨: labeling/labels.txt (무작위 표본 1,500건을 LLM(Claude)이 직접 읽고 분류)
- 표본: 불만 리뷰(1~3점, 본문 있음)를 order_id 순으로 앞에서 1,500건 → order_id 가 해시라 사실상 무작위
- 평가: 5-fold 층화 교차 검증 (정확도, macro F1, 유형별 정밀도·재현율)
- 결과: review_labels 테이블 (정답 표본은 source='manual', 나머지는 source='model' + 확신도)
"""
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import FeatureUnion, make_pipeline
from sqlalchemy import text

from config import ROOT, get_engine

CODES = {
    "D": "delivery_delay", "N": "not_received", "B": "damaged_defective", "W": "wrong_or_missing_item",
    "Q": "quality_mismatch", "S": "seller_service", "P": "positive_or_neutral", "O": "other",
}
MODEL_NAME = "tfidf-logreg"

TARGET_SQL = """
SELECT order_id, review_score, CONCAT_WS(' / ', review_comment_title, review_comment_message) AS body
FROM stg_order_reviews
WHERE review_score <= 3 AND review_comment_message IS NOT NULL
ORDER BY order_id"""


def load_gold():
    codes = ""
    for line in (ROOT / "labeling" / "labels.txt").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            codes += line.split(":", 1)[1].strip()
    sample = pd.read_csv(ROOT / "labeling" / "sample_1500.csv")
    assert len(codes) == len(sample), f"라벨 {len(codes)}건 / 표본 {len(sample)}건 불일치"
    sample["label"] = [CODES[c] for c in codes]
    return sample


def build_model():
    features = FeatureUnion([
        # 단어·두 단어 묶음: "nao recebi", "veio errado" 같은 표현
        ("word", TfidfVectorizer(strip_accents="unicode", lowercase=True, ngram_range=(1, 2), min_df=2, sublinear_tf=True)),
        # 글자 조각: 오타·붙여쓰기("naorecebi", "recebir")에 강함
        ("char", TfidfVectorizer(strip_accents="unicode", lowercase=True, analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True)),
    ])
    return make_pipeline(features, LogisticRegression(C=8, max_iter=3000, class_weight="balanced"))


def text_of(df):
    # 별점도 단서가 되므로 텍스트 앞에 토큰으로 붙인다
    return (df["review_score"].astype(str).radd("score") + " " + df["body"]).tolist()


def main():
    gold = load_gold()
    X, y = text_of(gold), gold["label"]

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    pred = cross_val_predict(build_model(), X, y, cv=cv)
    report = classification_report(y, pred, digits=3, output_dict=True, zero_division=0)
    print(f"5-fold 교차 검증: 정확도 {report['accuracy']:.3f}, macro F1 {report['macro avg']['f1-score']:.3f}")
    print(classification_report(y, pred, digits=3, zero_division=0))
    labels = list(CODES.values())
    cm = pd.DataFrame(confusion_matrix(y, pred, labels=labels), index=labels, columns=labels)

    model = build_model().fit(X, y)
    engine = get_engine()
    target = pd.read_sql(TARGET_SQL, engine)
    proba = model.predict_proba(text_of(target))
    target["complaint_type"] = model.classes_[proba.argmax(axis=1)]
    target["confidence"] = proba.max(axis=1).round(4)
    target["source"] = "model"
    # 사람이 직접 분류한 표본은 정답 라벨을 그대로 사용
    gold_map = gold.set_index("order_id")["label"]
    is_gold = target["order_id"].isin(gold_map.index)
    target.loc[is_gold, "complaint_type"] = target.loc[is_gold, "order_id"].map(gold_map)
    target.loc[is_gold, ["source", "confidence"]] = ["manual", 1.0]
    target["model"] = MODEL_NAME
    target["labeled_at"] = datetime.now().replace(microsecond=0)

    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS review_labels"))
        conn.execute(text("""
            CREATE TABLE review_labels (
                order_id        VARCHAR(32)  NOT NULL PRIMARY KEY,
                review_score    TINYINT      NOT NULL,
                complaint_type  VARCHAR(30)  NOT NULL,
                confidence      DECIMAL(5,4) NOT NULL,
                source          VARCHAR(10)  NOT NULL,   -- manual: 사람 라벨, model: 모델 예측
                model           VARCHAR(40)  NOT NULL,
                labeled_at      DATETIME     NOT NULL,
                INDEX ix_review_labels_type (complaint_type)
            )"""))
    cols = ["order_id", "review_score", "complaint_type", "confidence", "source", "model", "labeled_at"]
    target[cols].to_sql("review_labels", engine, if_exists="append", index=False, chunksize=5000, method="multi")

    # 평가 결과를 보고서로 남긴다
    lines = ["# 리뷰 불만 유형 분류 모델 평가", "",
             f"- 정답 데이터: 무작위 표본 {len(gold):,}건 (LLM 직접 라벨링)",
             f"- 모델: TF-IDF(단어 1~2gram + 글자 3~5gram) + 로지스틱 회귀",
             f"- 5-fold 층화 교차 검증: **정확도 {report['accuracy']:.3f}, macro F1 {report['macro avg']['f1-score']:.3f}**",
             f"- 적용 대상: 불만 리뷰 {len(target):,}건 (표본 {int(is_gold.sum()):,}건은 정답 라벨 사용)", "",
             "## 유형별 성능", "", "| 유형 | 정밀도 | 재현율 | F1 | 표본 수 |", "|---|---:|---:|---:|---:|"]
    for lab in labels:
        r = report[lab]
        lines.append(f"| {lab} | {r['precision']:.3f} | {r['recall']:.3f} | {r['f1-score']:.3f} | {int(r['support'])} |")
    lines += ["", "## 혼동 행렬 (행: 정답, 열: 예측)", "", "```", cm.to_string(), "```", ""]
    (ROOT / "review_model_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"적재 완료: review_labels {len(target):,}건 → review_model_report.md")


if __name__ == "__main__":
    main()
