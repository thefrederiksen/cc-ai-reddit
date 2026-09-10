# -*- coding: utf-8 -*-
"""cc-ai-reddit score: is this draft post likely to land, BEFORE it is sent.

A per-subreddit model predicts a POST's score from what is known at the moment
of posting: title and body length and word counts, "?" and "!" in the title,
the hour and weekday (UTC), the flair, and how many posts the author has made.
Nothing about meaning. The recipe was found by several hundred automated
experiments on r/ClaudeAI posts: a three-way classifier (score 0 / 1-2 / 3+,
LightGBM and XGBoost blended 67/33) and an XGBoost regressor on log1p(score).

BE STRAIGHT ABOUT THE ERROR. A prediction printed without its uncertainty is
trusted more than it deserves. Measured 2026-09-10 on the r/ClaudeAI model's
own temporal validation set: its point estimate is no better than always
guessing the median. What does carry signal is the ranking by p_high - the
validation posts it rated lowest scored 1 at the 90th percentile, the ones it
rated highest scored 85. So the output leads with the BAND (what validation
posts rated the same way actually scored) and prints the point estimate's
error beside it. Every number comes from the model's manifest, computed by
tools/train_model.py when the model was built or imported; none is typed here.

Models are pluggable, one directory per subreddit:
  <state>/models/<subreddit, lowercased>/
      model.json          manifest: subreddit, training window, validation metrics, bands
      clf_lgb.txt         LightGBM classifier
      clf_xgb.json        XGBoost classifier
      reg_xgb.json        XGBoost regressor
      flair_classes.json  the flair vocabulary; a flair's index is its encoded value
No pickle is ever loaded here. A model directory is data, not code.
"""
import datetime
import json
import os
import re

from .state import Fail, home, read_json

MODEL_KIND = "post-score-v1"
FEATURES = ["title_length", "title_word_count", "title_has_question_mark", "title_has_exclamation",
            "selftext_length", "selftext_word_count", "has_selftext", "has_url", "hour_utc",
            "day_of_week", "flair", "author_post_count"]
BLEND_LGB = 0.67
FILES = ("clf_lgb.txt", "clf_xgb.json", "reg_xgb.json", "flair_classes.json")


def deps():
    try:
        import numpy
        import pandas
        import lightgbm
        import xgboost
    except ImportError as exc:
        raise Fail("score needs numpy, pandas, lightgbm and xgboost (%s). Install them with: "
                   "pip install -e .[score]" % exc)
    return numpy, pandas, lightgbm, xgboost


def model_dir(subreddit):
    return os.path.join(home(), "models", subreddit.lower())


def parse_draft(text):
    """(title, body, flair) from a draft. The title must be stated: YAML
    frontmatter `title:`, or a first line `# Title`. A draft with neither has
    no title, and no first line is ever promoted to one by guesswork."""
    text = text.strip()
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if m:
        front, body = m.group(1), m.group(2).strip()
        t = re.search(r"^title:\s*(.+)$", front, re.M)
        f = re.search(r"^flair:\s*(.+)$", front, re.M)
        return ((t.group(1).strip().strip("\"'") if t else None), body,
                (f.group(1).strip().strip("\"'") if f else None))
    m = re.match(r"^#\s+(.+?)\s*\n+(.*)$", text + "\n", re.S)
    if m:
        return m.group(1).strip(), m.group(2).strip(), None
    return None, text, None


def feature_row(title, body, flair, author_posts, when, has_url=True):
    """ONE definition of the features, used to train and to score.

    has_url is the one that reads oddly and it is deliberate: in training it
    comes from the archive's `url` field, which a text post fills with its own
    permalink. It is 1 for about 99% of posts and says nothing about links in
    the body. A text draft is therefore scored with 1, matching what the model
    learned."""
    body = body or ""
    return {
        "title_length": len(title),
        "title_word_count": len(title.split()),
        "title_has_question_mark": int("?" in title),
        "title_has_exclamation": int("!" in title),
        "selftext_length": len(body),
        "selftext_word_count": len(body.split()),
        "has_selftext": int(len(body) > 0),
        "has_url": int(bool(has_url)),
        "hour_utc": when.hour,
        "day_of_week": when.weekday(),
        "flair": flair or "none",
        "author_post_count": int(author_posts or 0),
    }


class Model(object):

    def __init__(self, path):
        self.path = path
        self.manifest = read_json(os.path.join(path, "model.json"))
        if not self.manifest:
            raise Fail("no model at %s. Models are per subreddit and are not shipped with this tool; "
                       "build one with tools/train_model.py (see the README)." % path)
        if self.manifest.get("kind") != MODEL_KIND:
            raise Fail("%s is a %r model; this version reads %r" % (path, self.manifest.get("kind"), MODEL_KIND))
        missing = [f for f in FILES if not os.path.isfile(os.path.join(path, f))]
        if missing:
            raise Fail("model %s is incomplete, missing %s" % (path, ", ".join(missing)))
        np, pd, lgb, xgb = deps()
        self.np, self.pd = np, pd
        self.clf_lgb = lgb.Booster(model_file=os.path.join(path, "clf_lgb.txt"))
        self.clf_xgb = xgb.XGBClassifier()
        self.clf_xgb.load_model(os.path.join(path, "clf_xgb.json"))
        self.reg_xgb = xgb.XGBRegressor()
        self.reg_xgb.load_model(os.path.join(path, "reg_xgb.json"))
        with open(os.path.join(path, "flair_classes.json"), encoding="utf-8") as f:
            self.flairs = json.load(f)
        self.flair_index = {name: i for i, name in enumerate(self.flairs)}

    def frame(self, rows):
        df = self.pd.DataFrame(rows)[FEATURES].copy()
        df["flair"] = [self.flair_index.get(v, -1) for v in df["flair"]]
        return df

    def predict(self, df):
        """(probabilities n x 3, point estimates n) for an encoded frame."""
        np = self.np
        p_lgb = self.clf_lgb.predict(df)
        dx = df.copy()
        dx["flair"] = dx["flair"].astype("category")
        p_xgb = self.clf_xgb.predict_proba(dx)
        probs = BLEND_LGB * p_lgb + (1 - BLEND_LGB) * p_xgb
        reg = np.clip(np.expm1(self.reg_xgb.predict(dx)), 0, None)
        pred = np.clip(probs[:, 1] * 1.0 + probs[:, 2] * reg, 0, None)
        # The post-processing the experiments settled on, unchanged.
        pred = np.where((probs[:, 0] > 0.5) | ((pred < 0.3) & (probs[:, 0] > 0.3)), 0.0,
                        np.where(pred < 1.5, 1.0,
                                 np.where(pred < 15, np.floor(pred - 0.5),
                                          np.where(pred < 100, pred, np.round(pred)))))
        return probs, pred

    def band(self, p_high):
        bands = self.manifest["validation"]["bands"]
        for i, b in enumerate(bands):
            if p_high < b["p_high_max"] or i == len(bands) - 1:
                return i, b


def evaluate(model, df, y, train_median, n_bands=5):
    """Validation metrics and bands for a manifest. `df` is an encoded frame of
    posts the model never trained on, `y` their real scores."""
    np = model.np
    probs, pred = model.predict(df)
    y = np.asarray(y, dtype=float)
    err = np.abs(pred - y)
    edges = np.quantile(probs[:, 2], np.linspace(0, 1, n_bands + 1))
    bands = []
    for i in range(n_bands):
        lo, hi = edges[i], edges[i + 1]
        m = (probs[:, 2] >= lo) & ((probs[:, 2] < hi) if i < n_bands - 1 else (probs[:, 2] <= hi))
        ys = y[m]
        bands.append({"p_high_min": float(lo), "p_high_max": float(hi), "n": int(m.sum()),
                      "p10": float(np.percentile(ys, 10)), "p50": float(np.percentile(ys, 50)),
                      "p90": float(np.percentile(ys, 90)), "mean": float(ys.mean())})
    return {"posts": int(len(y)), "mae": float(err.mean()),
            "naive_median": float(train_median),
            "naive_median_mae": float(np.abs(train_median - y).mean()),
            "within": {str(k): float((err <= k).mean()) for k in (0, 1, 5, 10)},
            "bands": bands}


def run(a):
    """The `score` command."""
    try:
        with open(a.file, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        raise Fail("draft not found: %s" % a.file)
    title, body, flair = parse_draft(text)
    title = a.title or title
    flair = a.flair or flair
    if not title:
        raise Fail("score predicts POST scores and the draft states no title. Give it frontmatter "
                   "(title: ...), a first line '# Title', or --title. No model here predicts comment scores.")
    if a.when:
        try:
            when = datetime.datetime.strptime(a.when, "%Y-%m-%d %H:%M")
        except ValueError:
            raise Fail("--when must be 'YYYY-MM-DD HH:MM' in UTC")
    else:
        when = datetime.datetime.now(datetime.timezone.utc)
    model = Model(a.model_dir or model_dir(a.sub))
    man = model.manifest
    if man["subreddit"].lower() != a.sub.lower():
        raise Fail("the model at %s is for r/%s, not r/%s" % (model.path, man["subreddit"], a.sub))
    row = feature_row(title, body, flair, a.author_posts, when)
    probs, pred = model.predict(model.frame([row]))
    p0, p1, p2 = (float(x) for x in probs[0])
    idx, band = model.band(p2)
    val = man["validation"]
    known = flair is None or flair in model.flair_index
    print("r/%s draft post: %r" % (man["subreddit"], title[:90]))
    print("  flair %s%s, %d words, posting time %s UTC, author posts %d"
          % (flair or "none", "" if known else " (NOT SEEN IN TRAINING - scored as unknown)",
             row["selftext_word_count"], when.strftime("%Y-%m-%d %H:%M"), row["author_post_count"]))
    print("  p(score 0) %.2f   p(score 1-2) %.2f   p(score 3+) %.2f" % (p0, p1, p2))
    print("  BAND %d of %d by p(score 3+). Of the %d validation posts in this band, 10%% scored %g or "
          "less, half %g or less, 90%% %g or less." % (idx + 1, len(val["bands"]), band["n"], band["p10"],
                                                        band["p50"], band["p90"]))
    print("  Point estimate %g. Its validation error: MAE %.2f; always guessing %g scores MAE %.2f."
          % (pred[0], val["mae"], val["naive_median"], val["naive_median_mae"]))
    if val["mae"] >= val["naive_median_mae"]:
        print("  WARNING the point estimate is NOT better than a constant on validation. Read the band, "
              "not the number.")
    print("  model %s: trained on %s, validated on %s. Structural features only; it cannot read meaning."
          % (model.path, man.get("trained_on", {}).get("description"),
             man.get("validated_on", {}).get("description")))
    print("RESULT score sub=%s predicted=%g p_zero=%.3f p_low=%.3f p_high=%.3f band=%d/%d band_n=%d "
          "band_p10=%g band_p50=%g band_p90=%g mae=%.2f naive_mae=%.2f"
          % (man["subreddit"], pred[0], p0, p1, p2, idx + 1, len(val["bands"]), band["n"], band["p10"],
             band["p50"], band["p90"], val["mae"], val["naive_median_mae"]))
