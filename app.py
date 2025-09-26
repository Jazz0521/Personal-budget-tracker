
import os
from datetime import datetime, date
from collections import defaultdict

from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

# ---------------------- App & DB ----------------------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "budget.db")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY","dev-secret-key-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# ---------------------- Models ----------------------
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(120))
    password_hash = db.Column(db.String(255), nullable=False)

    transactions = db.relationship("Transaction", backref="user", lazy=True, cascade="all, delete")
    budgets = db.relationship("Budget", backref="user", lazy=True, cascade="all, delete")
    groups = db.relationship("Group", backref="owner", lazy=True, cascade="all, delete")

    def set_password(self, pwd):
        self.password_hash = generate_password_hash(pwd)

    def check_password(self, pwd):
        return check_password_hash(self.password_hash, pwd)

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    ttype = db.Column(db.String(10), nullable=False) # income | expense
    category = db.Column(db.String(80), nullable=False)
    note = db.Column(db.String(255))
    date = db.Column(db.Date, default=date.today)

class Budget(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    category = db.Column(db.String(80), nullable=False)
    limit = db.Column(db.Float, nullable=False)
    month = db.Column(db.String(7), nullable=False)  # YYYY-MM

class Group(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)

    members = db.relationship("GroupMember", backref="group", lazy=True, cascade="all, delete")
    expenses = db.relationship("GroupExpense", backref="group", lazy=True, cascade="all, delete")

class GroupMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("group.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)

class GroupExpense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("group.id"), nullable=False)
    description = db.Column(db.String(255), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    payer_id = db.Column(db.Integer, db.ForeignKey("group_member.id"), nullable=False)
    date = db.Column(db.Date, default=date.today)
    split_type = db.Column(db.String(20), default="equal")  # equal or ratio
    # Store per-member shares as JSON string: {"member_id": share}
    splits_json = db.Column(db.Text, nullable=True)

class GroupMemberProxy(db.Model):
    """Simple proxy so SQLAlchemy can create ForeignKey to GroupMember above as payer_id"""
    __tablename__ = "group_member"
    id = db.Column(db.Integer, primary_key=True)

# ---------------------- Auth ----------------------
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ---------------------- Routes (Pages) ----------------------
@app.route("/")
@login_required
def index():
    return render_template("index.html")

@app.route("/groups/<int:group_id>")
@login_required
def group_page(group_id):
    group = Group.query.filter_by(id=group_id, owner_id=current_user.id).first_or_404()
    return render_template("group.html", group=group)

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for("index"))
        flash("Invalid credentials", "danger")
    return render_template("login.html")

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        name = request.form.get("name","").strip()
        password = request.form.get("password","")
        if User.query.filter_by(email=email).first():
            flash("Email already registered", "warning")
            return redirect(url_for("register"))
        u = User(email=email, name=name)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        flash("Account created. Please sign in.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

# ---------------------- API: Transactions ----------------------
@app.route("/api/transactions", methods=["GET","POST"])
@login_required
def transactions_api():
    if request.method == "POST":
        data = request.get_json(force=True)
        t = Transaction(
            user_id=current_user.id,
            amount=float(data["amount"]),
            ttype=data["ttype"],
            category=data["category"],
            note=data.get("note",""),
            date=datetime.strptime(data["date"], "%Y-%m-%d").date() if data.get("date") else date.today()
        )
        db.session.add(t); db.session.commit()
        return jsonify({"status":"ok","id":t.id})
    # GET with filters
    q = Transaction.query.filter_by(user_id=current_user.id)
    ttype = request.args.get("type")
    if ttype in ("income","expense"):
        q = q.filter_by(ttype=ttype)
    category = request.args.get("category")
    if category:
        q = q.filter_by(category=category)
    start = request.args.get("start")
    end = request.args.get("end")
    if start:
        q = q.filter(Transaction.date >= datetime.strptime(start, "%Y-%m-%d").date())
    if end:
        q = q.filter(Transaction.date <= datetime.strptime(end, "%Y-%m-%d").date())
    search = request.args.get("q")
    if search:
        q = q.filter(Transaction.note.like(f"%{search}%"))
    items = q.order_by(Transaction.date.desc(), Transaction.id.desc()).all()
    return jsonify([{
        "id":t.id, "amount":t.amount, "type":t.ttype, "category":t.category,
        "note":t.note, "date":t.date.isoformat()
    } for t in items])

@app.route("/api/transactions/<int:tid>", methods=["PUT","DELETE"])
@login_required
def transactions_one(tid):
    t = Transaction.query.filter_by(id=tid, user_id=current_user.id).first_or_404()
    if request.method == "DELETE":
        db.session.delete(t); db.session.commit()
        return jsonify({"status":"deleted"})
    data = request.get_json(force=True)
    t.amount = float(data.get("amount", t.amount))
    t.ttype = data.get("ttype", t.ttype)
    t.category = data.get("category", t.category)
    t.note = data.get("note", t.note)
    if data.get("date"):
        t.date = datetime.strptime(data["date"], "%Y-%m-%d").date()
    db.session.commit()
    return jsonify({"status":"updated"})

# ---------------------- API: Budgets ----------------------
@app.route("/api/budgets", methods=["GET","POST"])
@login_required
def budgets_api():
    if request.method == "POST":
        data = request.get_json(force=True)
        b = Budget(
            user_id=current_user.id,
            category=data["category"],
            limit=float(data["limit"]),
            month=data["month"]
        )
        db.session.add(b); db.session.commit()
        return jsonify({"status":"ok","id":b.id})
    items = Budget.query.filter_by(user_id=current_user.id).order_by(Budget.month.desc()).all()
    return jsonify([{"id":b.id,"category":b.category,"limit":b.limit,"month":b.month} for b in items])

@app.route("/api/budgets/<int:bid>", methods=["PUT","DELETE"])
@login_required
def budgets_one(bid):
    b = Budget.query.filter_by(id=bid, user_id=current_user.id).first_or_404()
    if request.method == "DELETE":
        db.session.delete(b); db.session.commit()
        return jsonify({"status":"deleted"})
    data = request.get_json(force=True)
    b.category = data.get("category", b.category)
    b.limit = float(data.get("limit", b.limit))
    b.month = data.get("month", b.month)
    db.session.commit()
    return jsonify({"status":"updated"})

# ---------------------- API: Summary & Reports ----------------------
def month_key(d: date):
    return d.strftime("%Y-%m")

@app.route("/api/summary")
@login_required
def summary_api():
    # Category totals for a month
    month = request.args.get("month")
    q = Transaction.query.filter_by(user_id=current_user.id)
    if month:
        y, m = month.split("-")
        start = date(int(y), int(m), 1)
        if int(m) == 12:
            end = date(int(y)+1, 1, 1)
        else:
            end = date(int(y), int(m)+1, 1)
        q = q.filter(Transaction.date >= start, Transaction.date < end)
    tx = q.all()
    by_cat = defaultdict(float)
    income_monthly = defaultdict(float)
    expense_monthly = defaultdict(float)
    trend_monthly = defaultdict(float)

    for t in tx:
        mk = month_key(t.date)
        if t.ttype == "expense":
            by_cat[t.category] += t.amount
            expense_monthly[mk] += t.amount
            trend_monthly[mk] += t.amount
        else:
            income_monthly[mk] += t.amount

    # Budgets usage for given month
    budget_rows = Budget.query.filter_by(user_id=current_user.id, month=month).all() if month else []
    budgets = []
    for b in budget_rows:
        used = by_cat.get(b.category, 0.0)
        pct = round(100*used/b.limit, 2) if b.limit else 0.0
        budgets.append({"category": b.category, "limit": b.limit, "used": used, "percent": pct})

    # Ensure months sorted
    months = sorted(set(list(income_monthly.keys()) + list(expense_monthly.keys()) + list(trend_monthly.keys())))
    income = [round(income_monthly[m],2) for m in months]
    expense = [round(expense_monthly[m],2) for m in months]
    trend = [round(trend_monthly[m],2) for m in months]

    return jsonify({
        "categoryTotals": [{"category":k, "total":round(v,2)} for k,v in sorted(by_cat.items())],
        "months": months,
        "incomeSeries": income,
        "expenseSeries": expense,
        "trendSeries": trend,
        "budgets": budgets
    })

# ---------------------- API: Groups ----------------------
@app.route("/api/groups", methods=["GET","POST"])
@login_required
def groups_api():
    if request.method == "POST":
        data = request.get_json(force=True)
        g = Group(owner_id=current_user.id, name=data["name"])
        db.session.add(g); db.session.commit()
        return jsonify({"status":"ok","id":g.id})
    groups = Group.query.filter_by(owner_id=current_user.id).all()
    return jsonify([{"id":g.id,"name":g.name} for g in groups])

@app.route("/api/groups/<int:gid>/members", methods=["GET","POST"])
@login_required
def group_members_api(gid):
    g = Group.query.filter_by(id=gid, owner_id=current_user.id).first_or_404()
    if request.method == "POST":
        data = request.get_json(force=True)
        m = GroupMember(group_id=g.id, name=data["name"])
        db.session.add(m); db.session.commit()
        return jsonify({"status":"ok","id":m.id})
    ms = GroupMember.query.filter_by(group_id=g.id).all()
    return jsonify([{"id":m.id,"name":m.name} for m in ms])

@app.route("/api/groups/<int:gid>/expenses", methods=["GET","POST"])
@login_required
def group_expenses_api(gid):
    g = Group.query.filter_by(id=gid, owner_id=current_user.id).first_or_404()
    if request.method == "POST":
        data = request.get_json(force=True)
        ge = GroupExpense(
            group_id=g.id,
            description=data["description"],
            amount=float(data["amount"]),
            payer_id=int(data["payer_id"]),
            date=datetime.strptime(data["date"], "%Y-%m-%d").date() if data.get("date") else date.today(),
            split_type=data.get("split_type","equal"),
            splits_json=data.get("splits_json")
        )
        db.session.add(ge); db.session.commit()
        return jsonify({"status":"ok","id":ge.id})
    es = GroupExpense.query.filter_by(group_id=g.id).order_by(GroupExpense.date.desc()).all()
    return jsonify([{
        "id":e.id,"description":e.description,"amount":e.amount,"payer_id":e.payer_id,
        "date":e.date.isoformat(),"split_type":e.split_type,"splits_json":e.splits_json
    } for e in es])

@app.route("/api/groups/<int:gid>/settlements")
@login_required
def group_settlements_api(gid):
    g = Group.query.filter_by(id=gid, owner_id=current_user.id).first_or_404()
    members = {m.id: m.name for m in GroupMember.query.filter_by(group_id=g.id).all()}
    balances = defaultdict(float)  # positive => should receive
    expenses = GroupExpense.query.filter_by(group_id=g.id).all()
    if not members:
        return jsonify({"members": [], "balances": [], "transfers": []})

    for e in expenses:
        payer = e.payer_id
        splits = {}
        if e.split_type == "equal" or not e.splits_json:
            share = 1/len(members) if members else 0
            for mid in members.keys():
                splits[mid] = share
        else:
            try:
                raw = eval(e.splits_json) if isinstance(e.splits_json, str) else e.splits_json
            except Exception:
                raw = {}
            total_share = sum(float(v) for v in raw.values()) if raw else 0
            if total_share <= 0:
                share = 1/len(members)
                for mid in members.keys():
                    splits[mid] = share
            else:
                for k,v in raw.items():
                    splits[int(k)] = float(v)/total_share

        # payer paid amount
        balances[payer] += e.amount
        # everyone owes their share
        for mid, sh in splits.items():
            balances[mid] -= e.amount * sh

    # compute transfers using greedy
    creditors = [(mid, amt) for mid, amt in balances.items() if amt > 1e-6]
    debtors = [(mid, -amt) for mid, amt in balances.items() if amt < -1e-6]
    creditors.sort(key=lambda x: x[1], reverse=True)
    debtors.sort(key=lambda x: x[1], reverse=True)

    transfers = []
    i=j=0
    while i < len(debtors) and j < len(creditors):
        d_id, d_amt = debtors[i]
        c_id, c_amt = creditors[j]
        pay = round(min(d_amt, c_amt), 2)
        if pay > 0:
            transfers.append({"from": members[d_id], "to": members[c_id], "amount": pay})
            d_amt -= pay; c_amt -= pay
        if d_amt <= 1e-6: i += 1
        else: debtors[i] = (d_id, d_amt)
        if c_amt <= 1e-6: j += 1
        else: creditors[j] = (c_id, c_amt)

    return jsonify({
        "members":[{"id":mid,"name":name} for mid,name in members.items()],
        "balances":[{"member":members[mid], "net": round(amt,2)} for mid,amt in balances.items()],
        "transfers": transfers
    })

# ---------------------- Utilities ----------------------
@app.cli.command("init-db")
def init_db():
    """Initialize database tables."""
    db.create_all()
    print("Database initialized:", DB_PATH)

# Automatic table creation (for first run)
with app.app_context():
    db.create_all()

# -------------- Run --------------
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
