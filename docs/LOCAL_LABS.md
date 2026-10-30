# Running the labs on this machine

How we change the course's notebooks in `labs/` so they run here. There are two changes:

1. **truststore**, so HTTPS works behind this network, which inspects traffic.
2. **Local Postgres in Docker** in place of Supabase, wherever a lab uses a database.

To apply this file to a lab, ask: "Apply docs/LOCAL_LABS.md to labs/week2/week2day5.ipynb".
`labs/week2/week2day3.ipynb` and `labs/week2/week2day4.ipynb` are the worked examples.

## Rules for every lab

- **Add cells; don't rewrite the lab's own cells.** The one exception is the truststore lines at the top
  of the first code cell. Everything else goes in new cells beside the lab's, so the lab still reads as
  Ed wrote it and his updates merge cleanly.
- **Each new cell says who it is for.** A new markdown cell starts with a heading that begins
  `Local Postgres:` and an italic line such as *Only for Postgres in Docker.* A new code cell sits just
  under such a note.
- **Use `127.0.0.1`, never `localhost`.** On Windows, `localhost` tries IPv6 first, and each connection
  waits about a second or hangs.
- **The free version of a lab gets the same changes** (`week2day5free.ipynb` beside `week2day5.ipynb`).
- **Check only the cells you changed or added.** Run them from `labs/<week>/` with the project's Python.
  Don't run the parts that call a paid model. If you do run an experiment, set `TINY = True` first.
- **Commit each lab on its own**, with a message such as "Week 2 Day 5 lab: trust the Windows
  certificate store, and local Postgres in Docker". Never push unless asked.

## 1. truststore

**When:** every lab that reaches a service over HTTPS: OpenRouter, Langfuse, AWS, Supabase, Hugging Face.

**Where:** the very top of the first code cell, above the other imports. It must run before anything
opens a connection, or that connection keeps the old certificate check.

```python
import truststore

# Trust the certificates in the Windows certificate store, so HTTPS works behind a network that inspects traffic.
# This must run before anything opens a connection, or that connection keeps the old certificate check.
truststore.inject_into_ssl()
```

**Check:** the cell that connects to OpenRouter or Langfuse prints that it found its key or connected,
with no `SSLCertVerificationError`.

## 2. Local Postgres

The database is Postgres 17 in the Docker container `undercover-postgres`, on `127.0.0.1:5432`, with its
data in the Docker volume `undercover-pgdata`. `DATABASE_URL` in `.env` points at it. Docker Desktop must
be running.

Two schemas share the database:

- `lab` holds the labs' own tables and checkpoints.
- `undercover` holds the product's tables and the agent's checkpoints. A lab never writes there unless the
  lab is about the product's data.

### 2a. A note near the top

After the lab's "Before you start" box, add a markdown cell that says which parts of the lab use the
database and that they use the container in place of Supabase. If no part uses it, skip this.

### 2b. The start cell

Put it where the lab first needs the database, under a note headed
`### Local Postgres: an option in place of Supabase`. Copy it from cell 7 of
`labs/week2/week2day3.ipynb`. It needs `os`, `Path` and `load_dotenv` from the lab's imports, and
imports the rest itself. It:

- starts the container if it has stopped;
- makes it again with the password in `.env`, on the same volume, if Docker has lost it;
- creates it with a random password and adds `DATABASE_URL` to `.env` if there is none;
- leaves things alone if `DATABASE_URL` points somewhere else;
- waits until Postgres answers.

### 2c. The connection

LangGraph's checkpointer needs three settings on its connection. Keep a lab's tables in `lab`:

```python
connection = psycopg.connect(DATABASE_URL, autocommit=True, prepare_threshold=0, row_factory=dict_row, options="-c search_path=lab")
connection.execute("CREATE SCHEMA IF NOT EXISTS lab")
```

If the lab already has a cell like this for Supabase, leave it: the same cell works on the local
`DATABASE_URL`. Add your own only where the lab has none, as in `week2day4.ipynb`'s stretch challenge.

### 2d. Where the lab uses Supabase's own tools

Add a note cell beside each one saying what to do instead:

- **The Supabase MCP server** works only with a Supabase project. Its cell stops with an `IndexError` on a
  local `DATABASE_URL`. Say to skip it, and add a cell that prints a command for a terminal instead:
  `docker exec undercover-postgres psql -U postgres -c "\dt lab.*"`.
- **The Table Editor** doesn't exist locally. Add a cell that reads the same table through the lab's
  connection with `pd.DataFrame(connection.execute("SELECT ...").fetchall())`.
- **The connection limit.** Supabase's free plan allows 15 connections through the session pooler. Postgres
  in Docker allows 100 (`max_connections`). Where a lab talks about the limit, add a note saying so, and
  that the product still keeps to 4 connections in each process because it moves to a hosted database.

### 2e. Check

Run the start cell, the connection cell and any new cell. The start cell prints "Postgres is ready".
Count the lab's rows with `docker exec undercover-postgres psql -U postgres -c "..."` if it writes any.

## 3. Where each lab stands

Done:

- `week1/week1day2.ipynb`, `week2/week2day1.ipynb`, `week2/week2day2.ipynb`: truststore.
- `week2/week2day3.ipynb`, `week2/week2day4.ipynb`: truststore and local Postgres.

Truststore only (no database):

- `week1/week1day2free.ipynb`, `week1/week1day4.ipynb`, `week1/week1day4cpu.ipynb`,
  `week1/week1day4free.ipynb`, `week1/week1day5_lab1.ipynb`, `week1/week1day5_lab2.ipynb`,
  `week1/week1day5_lab2free.ipynb`
- `week2/week2day1free.ipynb`, `week2/week2day2free.ipynb`, `week2/week2day4free.ipynb` (also the
  connection-limit note)
- `week3/week3day1.ipynb`, `week3/week3day2_lab1.ipynb`, `week3/week3day2_lab1free.ipynb`,
  `week3/week3day3free.ipynb`, `week3/week3day5_lab2.ipynb`

Truststore and local Postgres:

- `week2/week2day3free.ipynb`: the same changes as `week2day3.ipynb`.

Needs a decision first (section 4):

- `week2/week2day5.ipynb`, `week2/week2day5free.ipynb`
- `week3/week3day1free.ipynb`, `week3/week3day2_lab2.ipynb`, `week3/week3day2_lab2free.ipynb`,
  `week3/week3day3.ipynb`, `week3/week3day4_lab1.ipynb`, `week3/week3day4_lab2.ipynb`,
  `week3/week3day4_lab2free.ipynb`, `week3/week3day5_lab1.ipynb`, `week3/week3day5_lab1free.ipynb`

## 4. Decisions before Week 2 Day 5 and Week 3

Local Postgres replaces Supabase's database, but not the rest of Supabase, and not a database the cloud
can reach. Decide these before changing those labs.

- **Week 2 Day 5 uses Supabase Auth** for the handlers' sign-in (`VITE_SUPABASE_URL`, the publishable key
  and sign-in tokens). Local Postgres has no sign-in service. The choices: keep a free Supabase project
  for sign-in only while the data stays local; or run Supabase's own stack locally with the Supabase CLI
  (`supabase start`), which brings Auth as well as Postgres.
- **Week 3 deploys to AWS** (Lambda and AgentCore Runtime). Code there can't reach `127.0.0.1` on this
  laptop. These labs also turn `DATABASE_URL` into Supabase's transaction pooler with
  `.replace(":5432/", ":6543/")`, which does nothing to a local address. The choices: use a hosted Postgres
  (a Supabase project) from Week 3 on; or take the course's free route, which uses Render in place of AWS
  and still needs a database the cloud can reach.

Watch out: whatever is chosen, the labs that only read the database from the laptop (such as counting
claims by status) can still use local Postgres. Only what runs in the cloud needs the hosted one.
