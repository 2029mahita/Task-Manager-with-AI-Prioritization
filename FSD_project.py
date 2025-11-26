import streamlit as st
import sqlite3
import pandas as pd
import datetime as dt

# ------------------------------------------------------------
#       CONFIG
# ------------------------------------------------------------
st.set_page_config(page_title="Task Manager with Time Analytics", layout="wide")
DB_FILE = "time_analytics_tasks.db"

# ------------------------------------------------------------
#       DATABASE INIT
# ------------------------------------------------------------
def get_conn():
    return sqlite3.connect(DB_FILE, check_same_thread=False)

conn = get_conn()
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    category TEXT,
    priority TEXT,
    status TEXT DEFAULT 'Pending',
    created_at TEXT,
    due_at TEXT,
    completed_at TEXT,
    predicted_minutes REAL,
    recurrence TEXT DEFAULT 'None'
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS work_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    start_time TEXT,
    end_time TEXT,
    duration_minutes REAL,
    FOREIGN KEY(task_id) REFERENCES tasks(id)
)
""")
conn.commit()

# ------------------------------------------------------------
#       HELPERS
# ------------------------------------------------------------
def now_iso(): return dt.datetime.now().isoformat(timespec="seconds")
fetch = lambda q,p=(): pd.read_sql_query(q, conn, params=p)

def get_pending(): return fetch("SELECT * FROM tasks WHERE status='Pending'")
def get_completed(): return fetch("SELECT * FROM tasks WHERE status='Completed'")
def get_sessions():
    return fetch("""
        SELECT ws.*,t.title,t.category FROM work_sessions ws
        LEFT JOIN tasks t ON ws.task_id=t.id
    """)

def avg_time_category():
    df = get_sessions()
    return {} if df.empty else df.groupby("category")["duration_minutes"].mean().to_dict()

def predict_time(cat):
    avg = avg_time_category()
    if cat in avg: return round(float(avg[cat]),1)
    if avg: return round(float(pd.Series(avg).mean()),1)
    return 30.0

def recur_create(old, type):
    if not old["due_at"]: return
    try: dt_old = dt.datetime.fromisoformat(old["due_at"])
    except: return
    new = dt_old + (dt.timedelta(days=1) if type=="Daily" else dt.timedelta(weeks=1) if type=="Weekly" else dt.timedelta())
    c.execute("""
    INSERT INTO tasks(title,description,category,priority,status,created_at,due_at,predicted_minutes,recurrence)
    VALUES(?,?,?,?, 'Pending',?,?,?,?)
    """,(old["title"],old["description"],old["category"],old["priority"],now_iso(),new.isoformat(),old["predicted_minutes"],type))
    conn.commit()

def complete(task_id, mins=None):
    row = fetch("SELECT * FROM tasks WHERE id=?", (task_id,)).iloc[0]
    c.execute("UPDATE tasks SET completed_at=?,status='Completed' WHERE id=?",(now_iso(),task_id))
    conn.commit()
    if mins and mins>0:
        c.execute("INSERT INTO work_sessions(task_id,start_time,end_time,duration_minutes) VALUES(?,?,?,?)",
                  (task_id,now_iso(),now_iso(),mins))
        conn.commit()
    if row["recurrence"]!="None": recur_create(row,row["recurrence"])

# ------------------------------------------------------------
# POMODORO STATE
# ------------------------------------------------------------
if "pom_task" not in st.session_state: st.session_state.pom_task=None
if "pom_start" not in st.session_state: st.session_state.pom_start=None
if "pom_minutes" not in st.session_state: st.session_state.pom_minutes=25

def pom_start(id):
    st.session_state.pom_task=id
    st.session_state.pom_start=now_iso()

def pom_stop(save=True):
    id=st.session_state.pom_task; start=st.session_state.pom_start
    st.session_state.pom_task=None; st.session_state.pom_start=None
    if not(save and id and start): return
    s=dt.datetime.fromisoformat(start); e=dt.datetime.now()
    mins=max(1,(e-s).total_seconds()/60)
    c.execute("INSERT INTO work_sessions(task_id,start_time,end_time,duration_minutes) VALUES(?,?,?,?)",
              (id,start,e.isoformat(timespec="seconds"),mins))
    conn.commit()

# ------------------------------------------------------------
# ANALYTICS
# ------------------------------------------------------------
def daily_scores():
    s=get_sessions()
    if s.empty: return pd.DataFrame(columns=["date","minutes","score"])
    s["start_time"]=pd.to_datetime(s["start_time"]); s["date"]=s["start_time"].dt.date
    d=s.groupby("date")["duration_minutes"].sum().reset_index()
    d["score"]=(d["duration_minutes"]/240*100).clip(upper=120).round(1)
    return d.sort_values("date",ascending=False)

def weekly_score():
    s=daily_scores()
    if s.empty:return 0
    td=dt.date.today(); wk=td-dt.timedelta(days=6)
    r=s[(s.date>=wk)&(s.date<=td)]
    return round(r.score.mean(),1) if not r.empty else 0

def best_hours():
    s=get_sessions()
    if s.empty:return []
    s["start_time"]=pd.to_datetime(s["start_time"]); s["hour"]=s["start_time"].dt.hour
    h=s.groupby("hour")["duration_minutes"].sum().sort_values(ascending=False).head(3)
    return [(f"{x:02d}:00-{x:02d}:59",round(float(y),1)) for x,y in h.items()]

# ------------------------------------------------------------
# UI
# ------------------------------------------------------------
st.title("⏱️ Task Manager with Time Analytics")
tab1,tab2,tab3,tab4=st.tabs(["📝 Tasks","📊 Dashboard","⏰ Pomodoro","🤖 AI Insights"])

# ------------------------------------------------------------
# TASK TAB
# ------------------------------------------------------------
with tab1:
    st.header("Create New Task")
    with st.form("new_task"):
        col1,col2=st.columns(2)
        with col1:
            title=st.text_input("Title *")
            cat=st.text_input("Category (optional)")
            priority=st.selectbox("Priority",["High","Medium","Low"])
        with col2:
            d=st.date_input("Due Date",dt.date.today())
            t=st.time_input("Due Time",dt.time(17,0))
            rec=st.selectbox("Recurrence",["None","Daily","Weekly"])
        desc=st.text_area("Description (optional)")
        pred=predict_time(cat) if cat.strip() else 30
        st.info(f"Estimated required time: {pred} minutes")

        if st.form_submit_button("Add Task"):
            if not title.strip(): st.error("Task title required")
            else:
                due=dt.datetime.combine(d,t).isoformat()
                c.execute("""
                INSERT INTO tasks(title,description,category,priority,status,created_at,due_at,predicted_minutes,recurrence)
                VALUES(?,?,?,?, 'Pending',?,?,?,?)
                """,(title.strip(), desc.strip() if desc else "", cat.strip(), priority,
                     now_iso(), due, pred, rec))
                conn.commit()
                st.success("Task Added Successfully")
                st.rerun()

    # Pending Tasks
    st.subheader("Pending Tasks")
    pend=get_pending()
    if pend.empty: st.info("No tasks pending")
    else:
        for _,x in pend.iterrows():
            with st.expander(f"{x.title} | {x.category} | Due: {x.due_at}"):
                st.write(x.description or "_No Description_")
                c1,c2,c3=st.columns(3)
                with c1:
                    if st.button("Mark Completed",key=f"done{x.id}"):
                        complete(x.id); st.rerun()
                with c2:
                    if st.button("Start Pomodoro",key=f"pom{x.id}"):
                        pom_start(x.id); st.rerun()
                with c3:
                    mins=st.number_input("Log Minutes",0.0,step=5.0,key=f"log{x.id}")
                    if st.button("Save Time",key=f"logb{x.id}") and mins>0:
                        complete(x.id,mins); st.success("Time Logged"); st.rerun()

    st.subheader("Completed Tasks")
    comp=get_completed()
    if comp.empty: st.caption("No completed tasks yet.")
    else: st.dataframe(comp.sort_values("completed_at",ascending=False),hide_index=True)

# ------------------------------------------------------------
# DASHBOARD TAB
# ------------------------------------------------------------
with tab2:
    st.header("📊 Productivity Overview")
    s=get_sessions()
    if s.empty: st.info("No work data yet.")
    else:
        st.subheader("Time spent per category")
        st.bar_chart(s.groupby("category")["duration_minutes"].sum())

        sc=daily_scores()
        if not sc.empty:
            col1,col2=st.columns(2)
            today=dt.date.today()
            today_score=float(sc[sc.date==today].score.iloc[0]) if today in sc.date.values else 0
            with col1: st.metric("Today Score",today_score)
            with col2: st.metric("Weekly Avg",weekly_score())
            st.line_chart(sc.set_index("date")["score"])

        st.subheader("Work Session History")
        st.dataframe(s.sort_values("start_time",ascending=False),hide_index=True)

# ------------------------------------------------------------
# POMODORO TAB
# ------------------------------------------------------------
with tab3:
    st.header("⏰ Pomodoro Timer - 25 min")
    tid=st.session_state.pom_task
    start=st.session_state.pom_start

    if tid and start:
        start_dt=dt.datetime.fromisoformat(start)
        total=25*60; elapsed=(dt.datetime.now()-start_dt).total_seconds()
        rem=max(0,total-elapsed)
        st.subheader(f"{int(rem//60):02d}:{int(rem%60):02d} remaining")
        st.progress(1-rem/total)

        c1,c2=st.columns(2)
        with c1:
            if st.button("Stop & Save"): pom_stop(True); st.rerun()
        with c2:
            if st.button("Cancel"): pom_stop(False); st.rerun()
    else:
        st.info("Start Pomodoro from task list")

# ------------------------------------------------------------
# AI TAB
# ------------------------------------------------------------
with tab4:
    st.header("🤖 AI Productivity Assistant")

    col1,col2=st.columns(2)
    with col1:
        st.subheader("Predicted Completion Rates")
        avg=avg_time_category()
        st.table(pd.DataFrame(avg.items(),columns=["Category","Avg Minutes"])) if avg else st.info("Need more history")

    with col2:
        st.subheader("Best Time to Work")
        b=best_hours()
        [st.write(f"**{x}** → {m} mins productive") for x,m in b] if b else st.info("Work sessions needed")
