# Track A: Index Findings and Next Steps

Oct 14, 2026 · Patrick Haggerty
Live version: https://claude.ai/code/artifact/f2810753-3b3c-4e8e-8970-d7d7906251bb

## Where things stand

The vector-index work for Module 2 is done. On a clean 8-vCPU cluster, tuned ScaNN serves 39% more searches per second than HNSW at about 95% recall, from an index about a tenth the size. Tuning ScaNN's re-ranking step matters more than which index you choose: it nearly quadruples ScaNN's throughput.

The question was whether ScaNN and HNSW differ under dozens of concurrent searches when single searches look alike. They do, but only once two traps were fixed: AlloyDB's columnar engine took over vector searches, and the PostgreSQL planner abandons HNSW at high `ef_search`. The proposal doc carries the results as of Oct 14. The next job is checking the preview features the other modules depend on.

## What we measured

Under load, both indexes are limited by database CPU, so throughput follows the CPU each search needs. Setup: AlloyDB PostgreSQL 18, one 8-vCPU zonal instance, 38,445 scouting reports with 3,072-dimension embeddings, a Colab Enterprise client on 4 vCPU. Each run used 64 concurrent searches from four client processes, with the vector index forced.

| Index and setting | Recall@10 | Searches per second | p95 latency | Database time per search (one sample) | Index size |
| --- | --- | --- | --- | --- | --- |
| ScaNN tuned: 39 of 196 leaves, re-rank 50, 1 thread | 95.2% | 1,234 | 78 ms | 3 ms | 29 MB |
| HNSW in half precision, `ef_search` 160 | 95.7% | 888 | 111 ms | 4.4 ms | 315 MB |
| ScaNN defaults: 39 leaves, re-rank 500 | 99.0% | 327 | 271 ms | 11 ms | 29 MB |

- **The database was the limit, not the client.** Cloud Monitoring showed 95 to 98% instance CPU during every run. HNSW from one client process matched four processes (889 against 888 searches per second).
- **ScaNN's cost is the re-ranking step.** Re-ranking 500 candidates touches about 10,000 pages per search; 50 touches about 2,200. The number of leaves searched mainly sets recall. One or two search threads made no difference.
- **The planner abandons HNSW above `ef_search` 160.** It prices a full scan at 7,433 regardless of `ef_search`, but HNSW at 7,318 at 160 and 8,773 at 200. From 200 up it picks the full scan: 2.2 s a search instead of 17 ms, at 96.5% recall. ScaNN stayed on its index at every setting.
- **The columnar engine took over vector searches.** With it enabled, hourly auto-columnarization loaded the tables and the planner preferred a columnar full scan (0.6 to 2.2 s) to either index. This also produced 03's "ScaNN wins filtered" result, now withdrawn.
- **Notebook timings are mostly network.** A tuned ScaNN search measured 12.6 ms from Colab and 3 ms inside the database. Demos and labs should quote EXPLAIN ANALYZE or load-test numbers.
- **Loading fits easily.** The corpus loads in 26 to 52 s depending on method; with the relational core and the ScaNN index, about 3 minutes against an 11-minute budget.

Filtered searches (one season and SIGN, about 450 to 600 matching reports) have no single winner:

| Approach | Recall@10 | Searches per second |
| --- | --- | --- |
| HNSW forced, with iterative scan | 95.9% | 414 |
| Planner's own choice: B-tree lookup, exact sort | 100% | 338 |
| ScaNN forced, 98 leaves, default re-rank | 92.9% | 215 |
| HNSW forced, no iterative scan | 53.8% (5.5 of 10 rows) | not load-tested |

ScaNN's filtered plans apply the filter after the index scan, although `scann.enable_inline_filtering` is on. One unverified explanation: `scann.min_qualifying_rows` is 1,000, and this filter keeps fewer rows than that.

## Decisions made and applied

Six decisions are already in the repo, the proposal doc, or the way runs are done.

- **Columnar engine off by default.** The Terraform has a new variable, `enable_columnar_engine`, default false. When true it also sets `google_columnar_engine.enable_auto_columnarization` to off, so a module loads only the tables it chooses. That flag name is not yet checked against AlloyDB's supported-flags list; check it before the first apply with the variable on. The Cloud Monitoring API was added to the enabled APIs.
- **Embeddings stay on gemini-embedding-001.** AlloyDB's in-database embedding functions don't support gemini-embedding-2.
- **Student cluster stays at 8 vCPUs.** Loads and index builds fit easily, and larger shapes raise capacity and quota risk across a full room. Throughput scales with vCPUs; the ScaNN-to-HNSW ratio does not.
- **Proposal doc updated (Oct 14).** The measured section has the load-test table, the re-ranking lesson, the HNSW planner trap, filtered-search results and the columnar decision. Module 2's slides, demo and lab task 3 now match.
- **Benchmark notebooks are in the repo.** `pipeline/02-load-test`, `03-index-benchmark` and `04-index-diagnostics`. 04 runs its load from several client processes and reports database-side time (`pg_stat_statements`) and instance CPU (Cloud Monitoring).
- **How runs are reviewed.** Patrick runs a notebook end to end and pastes its handback; no per-cell checklists.

## Proposed next steps

Check the preview features first: the proposal's completion date is Nov 16, about five weeks out, and most modules rest on features nobody has run yet.

1. **Feature checks, one notebook per module, this week.** Each runs in a fresh lab project and reports what works, in the same run-and-paste-handback pattern as 02 to 04. Start with Module 1, which has the most provisioning risk.
    - Module 1: Database Migration Service from SQL Server to AlloyDB with Gemini conversion; Cloud SQL for SQL Server in a lab project.
    - Module 2: `pg_textsearch` BM25, `ai.initialize_embeddings`, `ai.if` and `ai.rank`, PostgreSQL 19 for the graph task.
    - Module 3: the managed MCP server; QueryData context sets.
    - Module 4: Iceberg federation; the Gemini Enterprise trial and its AlloyDB connector.
2. **ScaNN filtered-search check, folded into the Module 2 notebook.** Lower `scann.min_qualifying_rows`, add a wider filter (one season, any recommendation, about 2,700 rows), and compare plans and recall. Google positions ScaNN on filtered search, so we should know whether our weak result is our setup before Google's AlloyDB team asks.
3. **Module 2 detailed design and notebook prototype.** It is the module closest to buildable: data, timings and lessons are measured. Time each task against the 120-minute lab, then prototype the notebook from 02's loading and 04's tuning code. It sets the notebook pattern for the other SQL modules.
4. **Module 1 provisioning decision.** Either Start Lab builds the cluster, or students run the Terraform. The choice changes Module 1's lab and the 15 to 20 minute cold-start promise.
5. **Housekeeping.** Push the repo; add the class-demo write grant to the setup checklist so results files save; resolve Deepak's open comment on football as the dataset.

Steps 1 and 2 run best in new conversations, one per module, each starting from this doc. They need little of this conversation's context beyond what is written here.

## Questions for the planning conversation

These need a view across all five modules, which this conversation didn't have.

1. **Is the step order right?** Would you check features before designing Module 2, or design Module 2 first and check features module by module as each is designed?
2. **Does Module 2 still fit 120 minutes?** Task 3 now covers tuning two ScaNN settings, the HNSW planner trap and a filtered-search comparison. Should the filtered comparison move to the demo?
3. **Which story leads the Module 2 demo?** Google's brief asks for a ScaNN-against-HNSW benchmark (2.a.1). The strongest measured result is that tuning ScaNN's re-ranking outweighs the choice of index.
4. **How should filtered search be presented** if the check confirms ScaNN trails HNSW with iterative scan and a plain exact scan at this selectivity?
5. **Does any module besides Module 5 need the columnar engine?** It now stays off unless a module's provisioning turns it on.
6. **Can demos run on a larger cluster than the students'?** A 16-vCPU demo cluster would roughly double the throughput on screen; the ratios stay the same.
7. **Does the schedule hold?** Five weeks to Nov 16 for five modules, with feature checks still to run.

## Where everything lives

| What | Where |
| --- | --- |
| Repo | [haggman/cymbalgoal-alloydb-workshop](https://github.com/haggman/cymbalgoal-alloydb-workshop), local clone in DevWork |
| Notebooks | `pipeline/00-explore-and-size` through `pipeline/04-index-diagnostics` |
| Terraform | `terraform/`, with the `enable_columnar_engine` variable in `variables.tf` |
| Scouting-report corpus | `gs://class-demo/alloydb-labs/cymbalgoal-alloydb-workshop/scout-reports/staged/` |
| Results files | `benchmarks/` and `load-tests/` under the same prefix; the Oct 14 runs did not save (no write grant) |
| Read-only source data | `gs://class-demo/alloydb-labs/cymbalgoal/`, used by another team's labs and never written |
| Proposal | [AlloyDB Cloud Track — Workshop Proposal and Technical Design](https://docs.google.com/document/d/1Ch2o6Zk0e2IeE7gGJ3_W2VXhDzkCZH0eZ5mocDM-o6A/edit) |
| Rebuild checklist | [CymbalGoal Clean Rebuild](https://claude.ai/artifact/G2AFzVqL5kMAZfQtqTJAmc) |

The run handbacks exist only in the conversation that produced this doc; the numbers above are taken from them.
