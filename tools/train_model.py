# -*- coding: utf-8 -*-
"""Build or import a per-subreddit model for `cc-ai-reddit score`.

  py -3.11 tools/train_model.py download <sub> --since YYYY-MM --data DIR
  py -3.11 tools/train_model.py build    <sub> --data DIR --holdout-from YYYY-MM-DD [--out DIR]
  py -3.11 tools/train_model.py import   <sub> --from DIR --holdout-from YYYY-MM-DD [--out DIR]

download  Every post of a subreddit from the Arctic Shift archive, one calendar
          month at a time. Monthly windows because one search pages out at
          roughly 3000 results; cursor paging inside the month; each finished
          month is written to its own file once and skipped on the next run,
          so an interrupted download resumes where it stopped. Those three
          lessons come from an earlier scraper that learned each the hard way.
          A month is only written once its last post has had SETTLE_DAYS to
          collect votes: the archive records a score at ingest, seconds after
          posting, and only later carries the settled one (measured
          2026-09-10: a nine-hour-old thread read score 1 while week-old posts
          carried real scores).
build     Features (the ONE definition in cc_ai_reddit_kit/score.py), a temporal
          split - train before --holdout-from, validate from it - the two-stage
          model, and a manifest whose validation numbers are measured on posts
          the model never trained on.
import    An already-trained model: a directory holding model/clf_lgb.txt,
          model/clf_xgb.json, model/reg_xgb.json, model/label_encoder.pkl and
          prepared_data/train.parquet + val.parquet. The label encoder is a
          pickle. It is opened HERE, once, on the operator's explicit request,
          and rewritten as flair_classes.json, so `score` never loads a pickle.
          Validation is measured again on val.parquet, not copied from anywhere.

The download is polite by construction: one request at a time, spaced, with a
User-Agent naming this tool. Arctic Shift is a free service; for really large
histories use its monthly dumps instead.
"""
import argparse
import datetime
import glob
import json
import os
import re
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cc_ai_reddit_kit import archive, score as S  # noqa: E402
from cc_ai_reddit_kit.state import Fail, log, write_json_atomic  # noqa: E402

SETTLE_DAYS = 7
FIELDS = "id,title,author,selftext,url,score,num_comments,created_utc,link_flair_text"
UTC = datetime.timezone.utc


def month_starts(since, until):
    y, m = since
    while (y, m) <= until:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def month_bounds(y, m):
    start = datetime.datetime(y, m, 1, tzinfo=UTC)
    end = datetime.datetime(y + 1, 1, 1, tzinfo=UTC) if m == 12 else datetime.datetime(y, m + 1, 1, tzinfo=UTC)
    return int(start.timestamp()), int(end.timestamp())


def fetch_month(sub, after, before):
    rows, seen, cursor = [], set(), before
    while True:
        batch = archive.get("/api/posts/search", {"subreddit": sub, "after": after, "before": cursor,
                                                  "limit": 100, "sort": "desc", "fields": FIELDS},
                            ttl=None, cache=False).get("data") or []
        fresh = [p for p in batch if p["id"] not in seen]
        for p in fresh:
            seen.add(p["id"])
        rows.extend(fresh)
        if len(batch) < 100 or not fresh:
            return rows
        oldest = batch[-1]["created_utc"]
        if oldest >= cursor:
            return rows
        cursor = oldest


def cmd_download(a):
    since = tuple(int(x) for x in a.since.split("-"))
    now = datetime.datetime.now(UTC)
    out_dir = os.path.join(a.data, a.sub.lower())
    os.makedirs(out_dir, exist_ok=True)
    total = 0
    for y, m in month_starts(since, (now.year, now.month)):
        after, before = month_bounds(y, m)
        path = os.path.join(out_dir, "%04d-%02d.jsonl" % (y, m))
        if before > now.timestamp() - SETTLE_DAYS * 86400:
            log("%04d-%02d: not settled yet (scores need %d days); stopping here" % (y, m, SETTLE_DAYS))
            break
        if os.path.exists(path):
            continue
        rows = fetch_month(a.sub, after, before)
        tmp = path + ".partial"
        with open(tmp, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=True) + "\n")
        os.replace(tmp, path)
        total += len(rows)
        log("%04d-%02d: %d posts" % (y, m, len(rows)))
    files = sorted(glob.glob(os.path.join(out_dir, "*.jsonl")))
    print("RESULT download sub=%s months=%d new_posts=%d dir=%s" % (a.sub, len(files), total, out_dir))


def load_posts(data, sub):
    files = sorted(glob.glob(os.path.join(data, sub.lower(), "*.jsonl")))
    if not files:
        raise Fail("no downloaded months under %s; run the download step first" % os.path.join(data, sub.lower()))
    posts = []
    for path in files:
        with open(path, encoding="utf-8") as f:
            posts += [json.loads(line) for line in f if line.strip()]
    return posts


def rows_for(posts, author_counts):
    rows, ys = [], []
    for p in posts:
        body = p.get("selftext") or ""
        if body in ("[removed]", "[deleted]"):
            body = ""
        when = datetime.datetime.fromtimestamp(p["created_utc"], UTC)
        rows.append(S.feature_row(p.get("title") or "", body, p.get("link_flair_text"),
                                  author_counts.get(p.get("author"), 0), when,
                                  has_url=bool(re.search(r"https?://", p.get("url") or ""))))
        ys.append(p["score"])
    return rows, ys


def manifest_base(sub, source, n_train, n_val, holdout, extra=None):
    m = {"kind": S.MODEL_KIND, "subreddit": sub, "features": S.FEATURES, "source": source,
         "created": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
         "trained_on": {"posts": n_train, "before": holdout,
                        "description": "%d posts created before %s" % (n_train, holdout)},
         "validated_on": {"posts": n_val, "from": holdout,
                          "description": "%d posts created on or after %s" % (n_val, holdout)},
         "validation": None}
    m.update(extra or {})
    return m


def finish(out, manifest, val_rows, val_y, train_median):
    write_json_atomic(os.path.join(out, "model.json"), manifest)
    model = S.Model(out)
    manifest["validation"] = S.evaluate(model, model.frame(val_rows), val_y, train_median)
    write_json_atomic(os.path.join(out, "model.json"), manifest)
    v = manifest["validation"]
    print("RESULT model sub=%s out=%s train=%d val=%d mae=%.2f naive_mae=%.2f bands_p90=%s"
          % (manifest["subreddit"], out, manifest["trained_on"]["posts"], v["posts"], v["mae"],
             v["naive_median_mae"], "/".join("%g" % b["p90"] for b in v["bands"])))


def cmd_build(a):
    np, pd, lgb, xgb = S.deps()
    holdout = int(datetime.datetime.strptime(a.holdout_from, "%Y-%m-%d").replace(tzinfo=UTC).timestamp())
    posts = [p for p in load_posts(a.data, a.sub) if p.get("created_utc") is not None and p.get("score") is not None]
    train = [p for p in posts if p["created_utc"] < holdout]
    val = [p for p in posts if p["created_utc"] >= holdout]
    if len(train) < 500 or len(val) < 100:
        raise Fail("too little data to build or to measure a model: %d training posts before %s and %d "
                   "validation posts after. Download more months." % (len(train), a.holdout_from, len(val)))
    counts = {}
    for p in train:
        counts[p.get("author")] = counts.get(p.get("author"), 0) + 1
    tr_rows, tr_y = rows_for(train, counts)
    va_rows, va_y = rows_for(val, counts)
    flairs = sorted({r["flair"] for r in tr_rows})
    index = {f: i for i, f in enumerate(flairs)}
    X = pd.DataFrame(tr_rows)[S.FEATURES]
    X["flair"] = [index[f] for f in X["flair"]]
    y = np.asarray(tr_y, dtype=float)
    y_class = np.where(y == 0, 0, np.where(y <= 2, 1, 2))
    Xx = X.copy()
    Xx["flair"] = Xx["flair"].astype("category")
    out = a.out or S.model_dir(a.sub)
    os.makedirs(out, exist_ok=True)
    log("training LightGBM classifier on %d posts" % len(X))
    clf_lgb = lgb.LGBMClassifier(objective="multiclass", num_class=3, n_estimators=690, learning_rate=0.03,
                                 max_depth=7, num_leaves=63, min_child_samples=20, subsample=0.8,
                                 colsample_bytree=0.8, reg_alpha=1.0, reg_lambda=1.0, verbosity=-1)
    clf_lgb.fit(X, y_class, categorical_feature=["flair"])
    log("training XGBoost classifier")
    clf_xgb = xgb.XGBClassifier(objective="multi:softprob", num_class=3, n_estimators=500, learning_rate=0.03,
                                max_depth=7, subsample=0.8, colsample_bytree=0.8, reg_alpha=0.5,
                                reg_lambda=0.5, verbosity=0, enable_categorical=True)
    clf_xgb.fit(Xx, y_class)
    log("training XGBoost regressor")
    reg = xgb.XGBRegressor(objective="reg:absoluteerror", n_estimators=5480, learning_rate=0.06, max_depth=5,
                           subsample=0.8, colsample_bytree=0.8, reg_alpha=0.8, reg_lambda=1.2, verbosity=0,
                           enable_categorical=True)
    reg.fit(Xx, np.log1p(y))
    clf_lgb.booster_.save_model(os.path.join(out, "clf_lgb.txt"))
    clf_xgb.save_model(os.path.join(out, "clf_xgb.json"))
    reg.save_model(os.path.join(out, "reg_xgb.json"))
    write_json_atomic(os.path.join(out, "flair_classes.json"), flairs)
    man = manifest_base(a.sub, "built by tools/train_model.py from the Arctic Shift archive", len(train),
                        len(val), a.holdout_from, {"settle_days": SETTLE_DAYS})
    finish(out, man, va_rows, va_y, float(np.median(y)))


def cmd_import(a):
    np, pd, lgb, xgb = S.deps()
    src_model = os.path.join(a.src, "model")
    src_data = os.path.join(a.src, "prepared_data")
    need = [os.path.join(src_model, f) for f in ("clf_lgb.txt", "clf_xgb.json", "reg_xgb.json", "label_encoder.pkl")]
    need += [os.path.join(src_data, f) for f in ("train.parquet", "val.parquet")]
    missing = [p for p in need if not os.path.isfile(p)]
    if missing:
        raise Fail("not an importable model directory, missing: %s" % ", ".join(missing))
    with open(need[0], "rb") as f:
        if b"\r\n" in f.read(1 << 20):
            raise Fail("%s has CRLF line endings, which LightGBM cannot read ('Model format error'). Git "
                       "converted it on checkout: clone again with -c core.autocrlf=false." % need[0])
    import pickle
    with open(need[3], "rb") as f:
        encoder = pickle.load(f)
    flairs = [str(c) for c in encoder.classes_]
    out = a.out or S.model_dir(a.sub)
    os.makedirs(out, exist_ok=True)
    for name in ("clf_lgb.txt", "clf_xgb.json", "reg_xgb.json"):
        shutil.copyfile(os.path.join(src_model, name), os.path.join(out, name))
    write_json_atomic(os.path.join(out, "flair_classes.json"), flairs)
    train = pd.read_parquet(need[4])
    val = pd.read_parquet(need[5])
    man = manifest_base(a.sub, "imported from an existing trained model (%s)" % os.path.basename(os.path.normpath(a.src)),
                        len(train), len(val), a.holdout_from)
    finish(out, man, val[S.FEATURES].to_dict("records"), val["score"].tolist(), float(train["score"].median()))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("download")
    sp.add_argument("sub")
    sp.add_argument("--since", required=True, help="first month, YYYY-MM")
    sp.add_argument("--data", required=True, help="directory for the monthly files")
    sp.set_defaults(fn=cmd_download)
    sp = sub.add_parser("build")
    sp.add_argument("sub")
    sp.add_argument("--data", required=True)
    sp.add_argument("--holdout-from", required=True, help="YYYY-MM-DD: validate on posts from this date")
    sp.add_argument("--out", help="model directory (default: the tool's models directory)")
    sp.set_defaults(fn=cmd_build)
    sp = sub.add_parser("import")
    sp.add_argument("sub")
    sp.add_argument("--from", dest="src", required=True)
    sp.add_argument("--holdout-from", required=True, help="the date the source split train from validation")
    sp.add_argument("--out", help="model directory (default: the tool's models directory)")
    sp.set_defaults(fn=cmd_import)
    a = ap.parse_args()
    try:
        a.fn(a)
    except Fail as exc:
        print("FAIL %s" % exc, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
