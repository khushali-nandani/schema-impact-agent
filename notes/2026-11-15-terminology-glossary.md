# Terminology glossary (personal learning notes, gitignored)

Jaise-jaise naye terms aayenge, yahan add karte rahenge.

## 1. Testing

| Term | Matlab | Is project ka example |
|---|---|---|
| **Test** | Code ka ek chhota function jo check karta hai ki kuch expected result aata hai | `assert schema_impact.__version__ == "0.0.1"` |
| **assert** | "Ye true hona chahiye, nahi to test fail" | upar wali line |
| **Smoke test** | Sabse basic check: cheez chalu bhi hoti hai? Deep logic nahi dekhta | `test_smoke.py`: package import hota hai? |
| **Unit test** | Ek chhote hisse (ek function) ka test | "ek SELECT se column lineage sahi nikli?" |
| **Integration test** | Kai hisse saath mein chalke sahi kaam karte hain? | "SQL folder load -> lineage -> impact, poora flow" |
| **End-to-end (e2e) test** | Poora system user ki tarah chalake dekhna | PR diff se report tak (L5) |
| **Regression test** | Purana bug wapas na aaye, uske liye test | bug fix ke saath uska test add karna |
| **Fixture** | Test ke liye pehle se taiyaar data ya setup | `examples/toy_project/` ke tables aur view |
| **Parametrize** | Ek test function ko alag-alag inputs pe chalana, har input alag test ban jaata hai | `test_subpackages_import` ne 4 test banaye |
| **Edge case** | Ajeeb ya rare input jahan code toot sakta hai | `SELECT *`, alias, nested subquery |
| **Test coverage** | Kitne % code ko tests chalate hain | `pytest --cov` (baad mein) |
| **Mock** | Asli cheez (jaise LLM API) ki jagah nakli cheez, taaki test fast aur free rahe | L2 mein LLM call mock karenge |
| **Ground truth** | Wo sahi jawab jisse hum apne agent ko compare karte hain | L3: `dbt build` se nikla breaking/silent/safe |
| **TDD** | Pehle test likho (fail hoga), phir code, jisse pass ho | hamare "test: ..." aur "feat: ..." alag commits |

## 2. Python project / packaging

| Term | Matlab |
|---|---|
| **Package** | `__init__.py` wala folder jo import ho sakta hai |
| **Module** | Ek `.py` file |
| **`__init__.py`** | Folder ko package banata hai, aur package import hone pe chalta hai |
| **src layout** | Code `src/` ke andar rakhna, taaki tests installed package ko import karein |
| **pyproject.toml** | Project ka config file: naam, deps, tool settings |
| **Dependency** | Wo library jispe tumhara code depend karta hai (sqlglot) |
| **Dev dependency** | Sirf development ke liye (pytest, ruff), users ko nahi chahiye |
| **Virtual environment (.venv)** | Is project ki alag Python installation, taaki libraries mix na hon |
| **Lockfile (uv.lock)** | Exact versions ki list, taaki sab ke machine pe same install ho |
| **Build backend (hatchling)** | Wo tool jo package ko build karta hai |
| **Docstring** | Function/module ke upar `"""..."""` wala description |
| **Type hints** | `def f(x: int) -> str:` jaise annotations, jo code ko padhne mein aur tools ke liye madad karte hain |

## 3. Code quality

| Term | Matlab |
|---|---|
| **Linter (ruff check)** | Code padhke galtiyan aur bad patterns batata hai (unused import) |
| **Formatter (ruff format)** | Code ko ek standard style mein likh deta hai (spaces, line length) |
| **Static analysis** | Code ko chalaye bina padhke check karna |
| **Lint rule codes** | `E`/`F`/`I`/`UP`/`B` jaise rule groups |

## 4. Git / GitHub

| Term | Matlab |
|---|---|
| **Repository (repo)** | Project ka folder jisme poori history bhi hai |
| **Commit** | Changes ka ek saved snapshot, ek message ke saath |
| **Branch** | Alag timeline, jahan main ko chhede bina kaam hota hai |
| **main** | Stable branch |
| **Pull Request (PR)** | "Meri branch ko main mein merge karo" ki request, review ke saath |
| **Merge** | Branch ke changes ko main mein jodna |
| **Staged / unstaged** | Staged = agle commit mein jaane ke liye chuni gayi files |
| **Push / pull** | Local commits GitHub pe bhejna / GitHub se laana |
| **Tag** | Kisi commit pe permanent label (`v0.1.0`) |
| **Issue** | GitHub pe ek chhota task ya bug ka card |
| **Milestone** | Issues ka group (L0, L1, ...) |
| **Conventional commits** | Message format: `feat:`, `fix:`, `test:`, `docs:`, `chore:` |
| **.gitignore** | Files jo git track nahi karega |
| **Semantic versioning** | `MAJOR.MINOR.PATCH`, jaise `0.1.0` |

## 5. CI / automation

| Term | Matlab |
|---|---|
| **CI (Continuous Integration)** | Har push/PR pe automatic tests aur lint chalna |
| **GitHub Actions** | GitHub ka CI system |
| **Workflow** | `.github/workflows/*.yml` ek automation recipe |
| **Green / red build** | Sab pass = green, kuch fail = red |
| **Badge** | README ka chhota status image ("CI passing") |

## 6. Data / SQL domain (is project ke core terms)

| Term | Matlab |
|---|---|
| **Schema** | Tables aur unke columns/types ka structure |
| **Model (dbt)** | Ek SQL file jo ek table/view banati hai |
| **Upstream / downstream** | Upstream = jahan se data aata hai, downstream = jo use padhta hai |
| **Consumer** | Koi bhi SQL/dashboard jo table ya column padhta hai |
| **Lineage** | Data ka raasta: kaun si cheez kisse bani |
| **Column-level lineage** | Lineage column tak, sirf table tak nahi |
| **DAG** | Directed Acyclic Graph: arrows wala graph jisme loop nahi hota. dbt ka model graph |
| **AST** | Abstract Syntax Tree: SQL ko tree structure mein todna (sqlglot yahi banata hai) |
| **Parser** | SQL text ko AST mein badalne wala |
| **Dialect** | Alag databases ki SQL ki alag boli (DuckDB, Snowflake, BigQuery) |
| **Breaking change** | Query error de deti hai |
| **Silent change** | Query chalti hai par numbers galat |
| **Safe change** | Koi asar nahi |
| **Blast radius** | Ek change se kitna kuch affect hota hai |
| **Mutation testing** | Code/SQL mein jaan-bujhkar change karke dekhna ki system pakadta hai ya nahi |
| **Precision / recall** | Precision = jo flag kiya usme kitna sahi tha. Recall = jo asli problems thi unme se kitni pakdi |

## 7. LangGraph / agent (L2 mein kaam aayenge)

| Term | Matlab |
|---|---|
| **State** | Graph ka shared data, jise nodes padhte aur update karte hain |
| **Node** | Graph ka ek step (function) |
| **Edge** | Ek node se agle node ka raasta |
| **Reducer** | Rule ki do updates ko kaise combine karna hai (overwrite ya append) |
| **Checkpointer** | Har step ke baad state ka snapshot, taaki resume ho sake |
| **thread_id** | Ek run/conversation ki pehchaan |
| **interrupt()** | Graph ko rok kar insaan ki approval ka wait karna |
| **Human-in-the-loop** | Risky kaam pe insaan ki approval zaroori |
| **Store** | Long-term memory jo alag runs ke beech bachti hai |
| **Hallucination** | LLM ka confidently galat jawab dena |
