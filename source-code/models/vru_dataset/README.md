# VRU conflict dataset (P06.06)

Real SUMO runs with pedestrians crossing at the network's marked crossings, cyclists in bike lanes and
passenger vehicles; trajectory rows within 40 m of each intersection at 0.25 s. `truth.py` derives realized
post-encroachment times from them (ground truth, never a detector input).

    bash run_container.sh                 # 18 runs -> output/run-a (gitignored); --label run-b for a twin
    bash run_container.sh 20260918 --label smoke --workers 2   # one seed

Splits follow P03.08 (train 5 / validation 2 / test 2 seeds; a `peak` and an `off-peak` run per seed). A twin
rebuild is byte-identical. See `backend/analytics/README.md` (P06.06) for the truth definition, the modelling
assumptions (assertive-driver share, slow pedestrians) and what the dataset cannot contain (no cyclist-vehicle
conflict: SUMO's separated bike lanes give >= 2.35 m clearance and no path crossing).
