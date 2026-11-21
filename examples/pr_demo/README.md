# Schema impact: pull request demo

A small repository to see the [schema-impact Action](../../action.yml) comment on a pull request.
It holds a SQL project (three models over a few source tables, in `models/` and `schema.sql`) and
the workflow that runs the Action. Two patches are the changes of the two pull requests you open.

The tests of the main repository apply both patches to this project and check the comments shown
below, so they are what the tool produces. What they cannot check is GitHub itself: how the
comment looks on the page, the permissions of the token, and that the Action installs on a
runner. That is what this demo is for.

## 1. Make a repository from this folder

Create an empty repository on GitHub (an empty one, no README), then:

```sh
cp -r examples/pr_demo/. ../schema-impact-demo
cd ../schema-impact-demo
git init -b main
git add -A && git commit -m "demo project"
git remote add origin git@github.com:<you>/schema-impact-demo.git
git push -u origin main
```

`.github/workflows/schema-impact.yml` uses the Action as `khushali-nandani/schema-impact-agent@main`.
Until the Action is merged into `main` of that repository, point it at the branch that has it
(`@feat/cli`), and after a release at its tag. The easiest is for the repository that holds the Action to be public; using it from a private
one needs its access to be shared in its settings, which has not been tried here.

## 2. First pull request: a column is computed differently

```sh
git switch -c average-total
git apply patches/average-total.patch
git commit -am "Average the order amounts"
git push -u origin average-total
```

Open a pull request into `main`. The job `schema-impact` runs and the pull request gets this
comment. The check passes: the default is to fail only on a breaking change.

````markdown
<!-- schema-impact-agent -->
## Schema impact: 1 model needs review

**Changes**
- The logic of customer_totals.total changed

### Needs review

| Model | Columns | Used in | Caused by |
|---|---|---|---|
| `top_customers` | whole model, `total` | order_by, select, where | The logic of customer_totals.total changed |
````

`customer_totals.total` is now an average, and `top_customers` filters and sorts on it, so it is
listed, but nothing breaks.

## 3. Second pull request: a column is dropped

```sh
git switch main
git switch -c drop-customer-name
git apply patches/drop-customer-name.patch
git commit -am "Drop customers.name"
git push -u origin drop-customer-name
```

Open a pull request into `main`. The comment, and a failing check:

````markdown
<!-- schema-impact-agent -->
## Schema impact: 2 models breaking

**Changes**
- Column customers.name was dropped

### Breaking

| Model | Columns | Used in | Caused by |
|---|---|---|---|
| `customer_totals` | whole model, `name` | group_by, select | Column customers.name was dropped |
| `top_customers` | whole model, `name`, `total` | order_by, select, where | Column customers.name was dropped |

**How to fix**
- Restore customers.name, or change the SQL that uses it.
````

## 4. What to look at

- The comment is **one** comment per pull request. Push another commit to the first pull request
  (for example revert the patch): the same comment is edited, not added to. If the review then has
  nothing downstream, it is edited to say so.
- On the second pull request the check is red and the comment is there anyway: the job comments
  first and fails afterwards.
- Change `fail-on` in the workflow to `needs_review` and the first pull request fails too, or to
  `never` and nothing fails.
- A pull request from a fork: the token is read-only there, so no comment can be written. The
  review is in the job summary of the run and the log has a `warning:` line; the job does not fail.
  This is how it is built and tested against a fake GitHub, and has not been seen on GitHub yet.

## If there is no comment

- **Actions > the run > Schema impact step.** An error there (`error: ...`, exit code 2) means the
  review could not be made, for example the base commit is missing: the checkout needs
  `fetch-depth: 0`, as in the workflow.
- A `warning: could not comment (GitHub answered 403 ...)` means the token cannot write comments.
  In the repository's Settings > Actions > General, "Workflow permissions" must allow the
  `pull-requests: write` the workflow asks for. The review is in the job summary instead.
- A change that affects nothing downstream makes no comment on purpose.
