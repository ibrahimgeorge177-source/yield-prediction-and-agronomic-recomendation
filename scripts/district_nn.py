"""Entity-embedding MLP member of the plot-level ensemble.

Kept in its own module so the tree-only pipeline runs without torch installed.

The three out-of-time fixes documented in notebook 06 §6.1 are all here, and they are the
difference between R2 = -0.02 and +0.14 on the 2020 season:

  * binary flags bypass the scaler and there is no BatchNorm over the inputs — a
    near-constant flag has a running variance close to zero, and a row where it fires
    arrives at the first layer as an enormous value;
  * rare levels (fewer than `min_count` training rows) and levels unseen at scoring time
    share one "unknown" embedding, which category dropout trains on real data — 2020
    introduced 11 districts absent from 2016-19, 14.5% of its rows;
  * embeddings start at std 0.01, so an untrained row is inert rather than random.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import r2_score

TARGET = "yield_kg_ph"


class EmbMLP(nn.Module):
    def __init__(self, cards, n_num, hidden=(128, 64), dropout=0.1, emb_scale=1.5):
        super().__init__()
        dims = [max(2, min(24, int(round(emb_scale * 1.6 * c ** 0.56)))) for c in cards]
        self.embs = nn.ModuleList([nn.Embedding(c, d) for c, d in zip(cards, dims)])
        for e in self.embs:
            nn.init.normal_(e.weight, std=0.01)
        self.emb_drop = nn.Dropout(0.05)
        layers, prev = [], sum(dims) + n_num
        for i, h in enumerate(hidden):
            layers += [nn.Linear(prev, h), nn.BatchNorm1d(h), nn.ReLU(),
                       nn.Dropout(dropout if i == 0 else dropout / 2)]
            prev = h
        self.body = nn.Sequential(*layers, nn.Linear(prev, 1))

    def forward(self, xn, xc):
        e = torch.cat([emb(xc[:, i]) for i, emb in enumerate(self.embs)], dim=1)
        return self.body(torch.cat([self.emb_drop(e), xn], dim=1)).squeeze(1)


@dataclass
class FittedNN:
    """Seed-bagged embedding MLP with everything needed to score an unseen frame."""
    schema: object
    recipe: object
    min_count: int = 10

    cont_cols_: list[str] = field(default_factory=list)
    binary_cols_: list[str] = field(default_factory=list)
    medians_: pd.Series | None = None
    mu_: pd.Series | None = None
    sd_: pd.Series | None = None
    cat_maps_: dict[str, dict] = field(default_factory=dict)
    cards_: list[int] = field(default_factory=list)
    weather_pca_: object = None
    y_mu_: float = 0.0
    y_sd_: float = 1.0
    states_: list[dict] = field(default_factory=list)
    arch_: dict = field(default_factory=dict)

    # ---- feature preparation ------------------------------------------------------
    def _numeric_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        s, k = self.schema, int((self.recipe.nn_params or {}).get("k", 3))
        num_cols = [c for c in (s.non_weather if k else s.feats) if c not in s.cats]
        base = df[num_cols].astype(float)
        if k and self.weather_pca_ is not None:
            z = self.weather_pca_["scaler"].transform(
                self.weather_pca_["imputer"].transform(df[s.weather]))
            pcs = pd.DataFrame(self.weather_pca_["pca"].transform(z), index=df.index,
                               columns=[f"wpc{i + 1}" for i in range(k)])
            base = pd.concat([base, pcs], axis=1)
        return base

    def _matrices(self, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        base = self._numeric_frame(df).fillna(self.medians_)
        cont = ((base[self.cont_cols_] - self.mu_) / self.sd_).clip(-5, 5).to_numpy()
        xn = (np.hstack([cont, (base[self.binary_cols_] > 0).astype(float).to_numpy()])
              if self.binary_cols_ else cont).astype(np.float32)
        codes = [df[c].astype(object).astype(str).map(self.cat_maps_[c]).fillna(0)
                 .astype(np.int64).to_numpy() for c in self.schema.cats]
        return xn, np.stack(codes, axis=1)

    # ---- fitting ------------------------------------------------------------------
    def fit(self, train: pd.DataFrame) -> "FittedNN":
        from sklearn.decomposition import PCA
        from sklearn.impute import SimpleImputer
        from sklearn.preprocessing import StandardScaler

        s = self.schema
        p = dict(self.recipe.nn_params or {})
        k = int(p.get("k", 3))
        if k:
            imp = SimpleImputer(strategy="median").fit(train[s.weather])
            sc = StandardScaler().fit(imp.transform(train[s.weather]))
            pca = PCA(n_components=k, random_state=0).fit(sc.transform(imp.transform(train[s.weather])))
            self.weather_pca_ = dict(imputer=imp, scaler=sc, pca=pca)

        base = self._numeric_frame(train)
        self.medians_ = base.median()
        base = base.fillna(self.medians_)
        nun = base.nunique()
        self.cont_cols_ = [c for c in base.columns if nun[c] > 2]
        self.binary_cols_ = [c for c in base.columns if c not in self.cont_cols_]
        self.mu_ = base[self.cont_cols_].mean()
        self.sd_ = base[self.cont_cols_].std().replace(0, 1)

        self.cat_maps_, self.cards_ = {}, []
        for c in s.cats:
            vc = train[c].astype(object).astype(str).value_counts()
            keep = [v for v in vc.index if vc[v] >= self.min_count]
            self.cat_maps_[c] = {v: i + 1 for i, v in enumerate(sorted(keep))}
            self.cards_.append(len(keep) + 1)

        xn, xc = self._matrices(train)
        yv = train[TARGET].to_numpy(np.float32)
        self.arch_ = dict(hidden=tuple(p.get("hidden", (128, 64))),
                          dropout=float(p.get("dropout", 0.1)),
                          emb_scale=float(p.get("emb_scale", 1.5)))

        folds = np.sort(train.cv_fold.dropna().unique()) if "cv_fold" in train else np.array([])
        self.states_ = []
        y_mus, y_sds = [], []
        for seed in range(int(self.recipe.nn_seeds)):
            if len(folds):
                es_fold = folds[seed % len(folds)]
                tr = (train.cv_fold != es_fold).to_numpy()
            else:
                rng = np.random.default_rng(seed)
                tr = rng.random(len(train)) > 0.2
            es = ~tr
            state, mu, sd = self._train_one(xn, xc, yv, tr, es, p, seed)
            self.states_.append(state)
            y_mus.append(mu), y_sds.append(sd)
        self.y_mu_, self.y_sd_ = float(np.mean(y_mus)), float(np.mean(y_sds))
        return self

    def _train_one(self, xn, xc, yv, tr, es, p, seed):
        rng = np.random.default_rng(seed)
        mu, sd = float(yv[tr].mean()), float(yv[tr].std())
        yt = (yv - mu) / sd

        torch.manual_seed(seed)
        model = EmbMLP(self.cards_, xn.shape[1], **self.arch_)
        opt = torch.optim.AdamW(model.parameters(), lr=float(p.get("lr", 3e-4)),
                                weight_decay=float(p.get("wd", 1e-5)))
        lossf = nn.HuberLoss(delta=1.0) if p.get("huber") else nn.MSELoss()
        bs, max_ep = int(p.get("batch", 512)), int(p.get("epochs", 80))
        cdrop, patience = float(p.get("cat_dropout", 0.15)), int(p.get("patience", 12))
        sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=max_ep)

        tn, tc, ty = torch.tensor(xn[tr]), torch.tensor(xc[tr]), torch.tensor(yt[tr])
        en, ec = torch.tensor(xn[es]), torch.tensor(xc[es])
        best, best_state, bad, n = -9e9, None, 0, len(ty)
        for _ in range(max_ep):
            model.train()
            perm = torch.tensor(rng.permutation(n))
            for i in range(0, n, bs):
                idx = perm[i:i + bs]
                if len(idx) < 8:
                    continue
                batch_c = tc[idx]
                if cdrop:
                    batch_c = torch.where(torch.rand(batch_c.shape) < cdrop,
                                          torch.zeros_like(batch_c), batch_c)
                opt.zero_grad()
                lossf(model(tn[idx], batch_c), ty[idx]).backward()
                opt.step()
            sched.step()
            model.eval()
            with torch.no_grad():
                score = r2_score(yv[es], model(en, ec).numpy() * sd + mu)
            if score > best:
                best, bad = score, 0
                best_state = {k: v.clone() for k, v in model.state_dict().items()}
            else:
                bad += 1
                if bad >= patience:
                    break
        return best_state, mu, sd

    # ---- prediction ---------------------------------------------------------------
    def predict(self, df: pd.DataFrame) -> np.ndarray:
        xn, xc = self._matrices(df)
        tn, tc = torch.tensor(xn), torch.tensor(xc)
        preds = []
        for state in self.states_:
            model = EmbMLP(self.cards_, xn.shape[1], **self.arch_)
            model.load_state_dict(state)
            model.eval()
            with torch.no_grad():
                preds.append(model(tn, tc).numpy() * self.y_sd_ + self.y_mu_)
        return np.clip(np.mean(preds, axis=0), *self.recipe.clip)
