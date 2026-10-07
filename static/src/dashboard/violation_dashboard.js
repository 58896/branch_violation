/** @odoo-module **/
/**
 * Violations Dashboard - the Violation Manager's one-glance screen.
 *
 * Every figure comes from one RPC (mm.violation.dashboard.get_dashboard_data).
 * Filters work three ways: the toolbar (period, status, search), the filter
 * drawer (every dimension, multi-select), and cross-filtering - clicking a
 * bar, slice, heatmap cell or name adds that value as a filter chip. Each
 * card's arrow opens the exact records behind it (the backend returns the
 * domain it used).
 *
 * Same visual family as marsamatrooh_pos_analytics (IBM Plex Sans Arabic,
 * ApexCharts, in-screen AR/EN toggle, RTL) without depending on it.
 */
import { registry } from "@web/core/registry";
import { loadJS } from "@web/core/assets";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";
import {
    Component, useState, useEffect, useRef, onWillStart, onWillUnmount,
    useExternalListener,
} from "@odoo/owl";

const VIEWS_KEY = "mm_vio_dash_views";
// filters survive the trip to a drill-down list and back (breadcrumb)
const STATE_KEY = "mm_vio_dash_state";
const AUTO_REFRESH_MS = 5 * 60 * 1000;
const APEX_URL = "/mm_branch_violation/static/lib/apexcharts/apexcharts.min.js";

const COUNTED = ["approved", "closed"];
const PIPELINE = ["draft", "under_review"];
const NOTE_TYPES = ["operational", "financial", "customer_complaint", "government"];
const DEPARTMENTS = ["operations", "finance", "customer_service"];
const STATES = ["draft", "under_review", "approved", "closed", "cancelled"];
const RESPONSE_STATES = ["pending", "contacted", "resolved", "unresolved"];

const C = {
    accent: "#5b3ec8", good: "#16a34a", warn: "#eda100", bad: "#d03b3b",
    ink2: "#6b7280", grid: "#eef0f3",
};
const NOTE_COLORS = {
    operational: "#5b3ec8", financial: "#eda100",
    customer_complaint: "#eb6834", government: "#d6336c",
};
const SERIES = ["#5b3ec8", "#eb6834", "#16a34a", "#63c6e0", "#eda100", "#12284c",
    "#d6336c", "#0ca678", "#f76707", "#4263eb", "#ae3ec9", "#74b816"];
const RESPONSE_COLORS = {
    pending: "#d03b3b", contacted: "#eda100", resolved: "#16a34a", unresolved: "#6b7280",
};

const I18N = {
    en: {
        title: "Violations Dashboard", subtitle: "Everything about branch violations, at a glance",
        updated: "Updated", refresh: "Refresh", fullscreen: "Full screen", report: "Report",
        scorecard: "Scorecard", newViolation: "New",
        // period
        today: "Today", last7: "Last 7 days", last30: "Last 30 days", thisMonth: "This month",
        lastMonth: "Last month", thisQuarter: "This quarter", thisYear: "This year",
        custom: "Custom range", from: "From", to: "To", apply: "Apply", cancel: "Cancel",
        auto: "Auto", day: "Day", week: "Week", month: "Month",
        // status
        counted: "Approved", pipeline: "In approval", all: "All logged",
        countedHint: "Approved + closed: what is deducted",
        // filters
        filters: "Filters", search: "Search employee, number, reference, customer...",
        clearAll: "Clear all", savedViews: "Saved views", saveView: "Save current view",
        viewName: "View name", noViews: "No saved views yet", save: "Save", delete: "Delete",
        branches: "Branches", employees: "Employees", types: "Violation types",
        sources: "Sources", jobs: "Job positions", noteTypes: "Category",
        departments: "Entered by", states: "Status", responseStates: "Complaint status",
        complaintsOnly: "Customer complaints only", repeatOnly: "Repeat offenders only",
        repeatOnlyHint: "Employees with 2 or more violations in the period",
        typeToSearch: "Type to search", selected: "selected", showResults: "Show results",
        reset: "Reset", period: "Period", status: "Status",
        // action center
        needsAttention: "Needs your attention", allClear: "All clear - nothing waiting",
        toReview: "Waiting for approval", oldest: "oldest %s days", staleDrafts: "Drafts not submitted",
        olderThan: "older than %s days", overdueComplaints: "Complaints past deadline",
        awaitingResponse: "Awaiting customer call", pendingDeductions: "Pending deduction",
        noCommission: "approved, no open commission yet", contacted: "Contacted, not closed",
        // kpis
        kViolations: "Violations", kPoints: "Deduction points", kEmployees: "Employees involved",
        kComplaints: "Customer complaints", kHandling: "Complaint handling",
        avgPerViolation: "avg %s per violation", repeatOffenders: "%s repeat offenders",
        acrossBranches: "across %s branches", resolvedPct: "Resolved", onTimePct: "On time",
        avgContact: "avg %s h to call back", vsPrev: "vs previous", noPrev: "no previous data",
        overdue: "%s overdue", pts: "pts",
        // sections
        insights: "Smart insights", noInsights: "Not enough data for insights in this period",
        funnel: "Approval pipeline", avgApprove: "avg %s h from submit to approval",
        trend: "Violations over time", prevPeriod: "Previous period", pointsSeries: "Points",
        heatmap: "Branch × violation type", heatHint: "Click a cell to filter by branch and type",
        other: "Other", total: "Total", byBranch: "Violations by branch",
        perEmployee: "per employee", staff: "staff", topType: "Top issue",
        breakdown: "Breakdown", pareto: "Top violation types (Pareto)", cumulative: "Cumulative %",
        weekday: "By weekday", offenders: "Employees to watch", employee: "Employee",
        branch: "Branch", count: "Count", points: "Points", lastSeen: "Last", daysAgo: "%sd ago",
        todayShort: "today", repeated: "×%s same issue", change: "Δ",
        complaintsPanel: "Customer complaints", byRep: "Customer service team",
        rep: "Person", unassigned: "Unassigned", complaintsCol: "Complaints", onTime: "On time",
        avgHours: "Avg hours", overdueList: "Past deadline - call now", hoursLate: "%s h late",
        recent: "Latest violations", ref: "Ref", date: "Date", type: "Violation",
        openList: "Open records", noData: "No violations match these filters",
        noDataHint: "Try a wider period or clear some filters.", loading: "Loading...",
        // insight templates
        insShare: "%1 has %2% of all violations (%3).",
        insUp: "Violations are up %1% vs the previous period (%2 → %3).",
        insDown: "Violations are down %1% vs the previous period (%2 → %3).",
        insTypeRise: "“%1” rose from %2 to %3.",
        insRepeat: "%1 repeated “%2” %3 times.",
        insOverdue: "%1 customer complaints are past their deadline.",
        insBacklog: "%1 violations wait for approval - the oldest for %2 days.",
        insPareto: "%1 violation types make %2% of all violations - fix these first.",
        insWeekday: "Most violations happen on %1 (%2%).",
        insClean: "%1 branches have no violations in this period.",
        wd: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
        wdLong: ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"],
        st: { draft: "New", under_review: "Under review", approved: "Approved", closed: "Closed", cancelled: "Cancelled" },
        nt: { operational: "Operational", financial: "Financial", customer_complaint: "Customer complaint", government: "Government" },
        dp: { operations: "Operations", finance: "Finance", customer_service: "Customer service" },
        rs: { pending: "Awaiting response", contacted: "Contacted", resolved: "Resolved", unresolved: "Unresolved" },
        bk: { note_types: "Category", departments: "Entered by", sources: "Source", jobs: "Job" },
    },
    ar: {
        title: "لوحة الملاحظات", subtitle: "كل ما يخص ملاحظات الفروع في نظرة واحدة",
        updated: "آخر تحديث", refresh: "تحديث", fullscreen: "ملء الشاشة", report: "التقرير",
        scorecard: "بطاقة الأداء", newViolation: "جديدة",
        today: "اليوم", last7: "آخر 7 أيام", last30: "آخر 30 يومًا", thisMonth: "هذا الشهر",
        lastMonth: "الشهر الماضي", thisQuarter: "هذا الربع", thisYear: "هذه السنة",
        custom: "فترة مخصصة", from: "من", to: "إلى", apply: "تطبيق", cancel: "إلغاء",
        auto: "تلقائي", day: "يوم", week: "أسبوع", month: "شهر",
        counted: "تم الإجراء", pipeline: "قيد الاعتماد", all: "كل المسجّل",
        countedHint: "تم الإجراء + مغلقة: ما يُخصم فعليًا",
        filters: "الفلاتر", search: "ابحث بالموظف أو الرقم الوظيفي أو المرجع أو العميل...",
        clearAll: "مسح الكل", savedViews: "العروض المحفوظة", saveView: "حفظ العرض الحالي",
        viewName: "اسم العرض", noViews: "لا توجد عروض محفوظة", save: "حفظ", delete: "حذف",
        branches: "المعارض", employees: "الموظفون", types: "أنواع المخالفات",
        sources: "مصادر الملاحظة", jobs: "المسميات الوظيفية", noteTypes: "نوع الملاحظة",
        departments: "الإدارة المدخلة", states: "الحالة", responseStates: "حالة الشكوى",
        complaintsOnly: "شكاوى العملاء فقط", repeatOnly: "المكررون فقط",
        repeatOnlyHint: "موظفون لديهم ملاحظتان أو أكثر في الفترة",
        typeToSearch: "اكتب للبحث", selected: "محدد", showResults: "عرض النتائج",
        reset: "إعادة تعيين", period: "الفترة", status: "الحالة",
        needsAttention: "يحتاج انتباهك", allClear: "لا يوجد ما ينتظر - كل شيء على ما يرام",
        toReview: "بانتظار الاعتماد", oldest: "الأقدم منذ %s يوم", staleDrafts: "مسودات لم تُرسل",
        olderThan: "أقدم من %s يوم", overdueComplaints: "شكاوى تجاوزت المهلة",
        awaitingResponse: "بانتظار الاتصال بالعميل", pendingDeductions: "بانتظار الخصم",
        noCommission: "تم الإجراء ولا توجد عمولة مفتوحة", contacted: "تم التواصل ولم تُغلق",
        kViolations: "الملاحظات", kPoints: "نقاط الخصم", kEmployees: "الموظفون المعنيون",
        kComplaints: "شكاوى العملاء", kHandling: "معالجة الشكاوى",
        avgPerViolation: "متوسط %s لكل ملاحظة", repeatOffenders: "%s موظف مكرر",
        acrossBranches: "في %s معارض", resolvedPct: "تم الحل", onTimePct: "في الوقت",
        avgContact: "متوسط %s ساعة للاتصال", vsPrev: "عن الفترة السابقة", noPrev: "لا بيانات سابقة",
        overdue: "%s متأخرة", pts: "نقطة",
        insights: "رؤى ذكية", noInsights: "لا توجد بيانات كافية لاستخراج رؤى في هذه الفترة",
        funnel: "مسار الاعتماد", avgApprove: "متوسط %s ساعة من الإرسال إلى الاعتماد",
        trend: "الملاحظات عبر الزمن", prevPeriod: "الفترة السابقة", pointsSeries: "النقاط",
        heatmap: "المعرض × نوع المخالفة", heatHint: "اضغط على خلية للتصفية بالمعرض والنوع",
        other: "أخرى", total: "الإجمالي", byBranch: "الملاحظات حسب المعرض",
        perEmployee: "لكل موظف", staff: "موظف", topType: "أكثر مخالفة",
        breakdown: "التوزيع", pareto: "أكثر المخالفات تكرارًا (باريتو)", cumulative: "النسبة التراكمية",
        weekday: "حسب أيام الأسبوع", offenders: "موظفون تحت المتابعة", employee: "الموظف",
        branch: "المعرض", count: "العدد", points: "النقاط", lastSeen: "آخر ملاحظة", daysAgo: "قبل %s يوم",
        todayShort: "اليوم", repeated: "×%s نفس المخالفة", change: "التغير",
        complaintsPanel: "شكاوى العملاء", byRep: "فريق خدمة العملاء",
        rep: "الموظف", unassigned: "غير مسندة", complaintsCol: "الشكاوى", onTime: "في الوقت",
        avgHours: "متوسط الساعات", overdueList: "تجاوزت المهلة - اتصل الآن", hoursLate: "متأخرة %s ساعة",
        recent: "أحدث الملاحظات", ref: "المرجع", date: "التاريخ", type: "المخالفة",
        openList: "فتح السجلات", noData: "لا توجد ملاحظات تطابق هذه الفلاتر",
        noDataHint: "جرّب فترة أطول أو امسح بعض الفلاتر.", loading: "جارٍ التحميل...",
        insShare: "%1 لديه %2% من كل الملاحظات (%3).",
        insUp: "الملاحظات ارتفعت %1% عن الفترة السابقة (%2 ← %3).",
        insDown: "الملاحظات انخفضت %1% عن الفترة السابقة (%2 ← %3).",
        insTypeRise: "«%1» ارتفعت من %2 إلى %3.",
        insRepeat: "%1 كرر «%2» %3 مرات.",
        insOverdue: "%1 شكاوى عملاء تجاوزت المهلة.",
        insBacklog: "%1 ملاحظات بانتظار الاعتماد - الأقدم منذ %2 يوم.",
        insPareto: "%1 أنواع مخالفات تمثل %2% من كل الملاحظات - ابدأ بها.",
        insWeekday: "أغلب الملاحظات تقع يوم %1 (%2%).",
        insClean: "%1 معارض بدون أي ملاحظة في هذه الفترة.",
        wd: ["أحد", "إثنين", "ثلاثاء", "أربعاء", "خميس", "جمعة", "سبت"],
        wdLong: ["الأحد", "الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت"],
        st: { draft: "جديدة", under_review: "تحت المراجعة", approved: "تم الإجراء", closed: "مغلقة", cancelled: "ملغاة" },
        nt: { operational: "تشغيلية", financial: "مالية", customer_complaint: "شكوى عميل", government: "مخالفة حكومية" },
        dp: { operations: "التشغيل", finance: "المالية", customer_service: "خدمة العملاء" },
        rs: { pending: "بانتظار الرد", contacted: "تم التواصل", resolved: "تم الحل", unresolved: "لم تحل" },
        bk: { note_types: "نوع الملاحظة", departments: "الإدارة المدخلة", sources: "المصدر", jobs: "الوظيفة" },
    },
};

// ---- dates (local, YYYY-MM-DD) ----
function ymd(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function today0() { const d = new Date(); d.setHours(0, 0, 0, 0); return d; }
function addDays(d, n) { const x = new Date(d); x.setDate(x.getDate() + n); return x; }
function presetRange(key) {
    const t = today0();
    const y = t.getFullYear(), m = t.getMonth();
    switch (key) {
        case "today": return [t, t];
        case "last7": return [addDays(t, -6), t];
        case "thisMonth": return [new Date(y, m, 1), t];
        case "lastMonth": return [new Date(y, m - 1, 1), new Date(y, m, 0)];
        case "thisQuarter": return [new Date(y, m - (m % 3), 1), t];
        case "thisYear": return [new Date(y, 0, 1), t];
        default: return [addDays(t, -29), t];  // last30
    }
}
const PRESETS = ["today", "last7", "last30", "thisMonth", "lastMonth", "thisQuarter", "thisYear"];

function emptyFilters() {
    return {
        branch_ids: [], employee_ids: [], type_ids: [], source_ids: [], job_ids: [],
        note_types: [], departments: [], response_states: [],
        complaints_only: false, repeat_only: false,
    };
}
function sameSet(a, b) {
    return a.length === b.length && a.every(x => b.includes(x));
}
function fmtStr(s, ...args) {
    // "%s" (single) or "%1 %2" (positional)
    if (s.includes("%1")) return args.reduce((acc, a, i) => acc.split(`%${i + 1}`).join(a), s);
    return args.reduce((acc, a) => acc.replace("%s", a), s);
}

export class ViolationDashboard extends Component {
    static template = "mm_branch_violation.ViolationDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.rootRef = useRef("root");
        this.refs = {};
        for (const k of ["sparkV", "sparkP", "sparkC", "gauge", "trend", "branch",
            "breakdown", "pareto", "weekday", "response"]) {
            this.refs[k] = useRef(k);
        }
        this._charts = [];

        const saved = this._loadSession();
        const [f, t] = presetRange("last30");
        this.state = useState({
            lang: this.odooLang,
            loading: true,
            data: null,
            preset: "last30",
            dateFrom: ymd(f),
            dateTo: ymd(t),
            granularity: null,      // null = auto from the period length
            states: [...COUNTED],
            filters: emptyFilters(),
            search: "",
            // ui
            periodOpen: false,
            customFrom: ymd(f),
            customTo: ymd(t),
            drawerOpen: false,
            draft: null,            // filters being edited in the drawer
            draftStates: [],
            drawerQ: { branches: "", employees: "", types: "", sources: "", jobs: "" },
            viewsOpen: false,
            viewName: "",
            views: this._loadViews(),
            breakdown: "note_types",
            updatedAt: null,
            ...saved,
        });
        this.state.searchInput = this.state.search;
        if (this.state.preset) {
            const [a, b] = presetRange(this.state.preset);
            this.state.dateFrom = ymd(a);
            this.state.dateTo = ymd(b);
        }

        useExternalListener(window, "keydown", (ev) => {
            if (ev.key !== "Escape") return;
            this.state.drawerOpen = false;
            this.state.periodOpen = false;
            this.state.viewsOpen = false;
        });
        onWillStart(async () => {
            if (typeof window.ApexCharts === "undefined") {
                try { await loadJS(APEX_URL); } catch { /* charts just stay empty */ }
            }
            await this.load();
        });
        useEffect(
            () => { this.renderCharts(); },
            () => [this.state.data, this.state.lang, this.state.breakdown, this.state.loading],
        );
        this._timer = setInterval(() => {
            if (!document.hidden && !this.state.drawerOpen) this.load(true);
        }, AUTO_REFRESH_MS);
        onWillUnmount(() => {
            clearInterval(this._timer);
            this.destroyCharts();
        });
    }

    // ------------------------------------------------------------------
    // i18n / formatting
    // ------------------------------------------------------------------
    get L() { return I18N[this.state.lang] || I18N.en; }
    t(k, ...args) {
        const s = this.L[k] ?? I18N.en[k] ?? k;
        return args.length ? fmtStr(s, ...args) : s;
    }
    get dir() { return this.state.lang === "ar" ? "rtl" : "ltr"; }
    get locale() { return this.state.lang === "ar" ? "ar-SA-u-nu-latn" : "en-US"; }
    n(v, dec = 0) {
        return Number(v || 0).toLocaleString(this.locale, { maximumFractionDigits: dec, minimumFractionDigits: 0 });
    }
    stLabel(k) { return this.L.st[k] || k; }
    ntLabel(k) { return this.L.nt[k] || k; }
    dpLabel(k) { return this.L.dp[k] || k; }
    rsLabel(k) { return this.L.rs[k] || k; }
    fmtDate(s) {
        if (!s) return "";
        const [y, m, d] = s.split("-").map(Number);
        return new Date(y, m - 1, d).toLocaleDateString(this.locale, { day: "numeric", month: "short" });
    }
    bucketLabel(s) {
        const g = this.data.granularity;
        if (g === "month") {
            const [y, m] = s.split("-").map(Number);
            return new Date(y, m - 1, 1).toLocaleDateString(this.locale, { month: "short", year: "2-digit" });
        }
        return this.fmtDate(s);
    }
    get periodLabel() {
        if (this.state.preset) return this.t(this.state.preset);
        return `${this.fmtDate(this.state.dateFrom)} – ${this.fmtDate(this.state.dateTo)}`;
    }
    get updatedLabel() {
        const d = this.state.updatedAt;
        return d ? d.toLocaleTimeString(this.locale, { hour: "2-digit", minute: "2-digit" }) : "";
    }

    // ------------------------------------------------------------------
    // persistence
    // ------------------------------------------------------------------
    /** The user's Odoo language (Preferences): Arabic -> ar, anything else -> en. */
    get odooLang() { return (user.lang || "").startsWith("ar") ? "ar" : "en"; }
    _loadSession() {
        try {
            const s = JSON.parse(window.sessionStorage.getItem(STATE_KEY) || "null");
            if (!s) return {};
            return {
                preset: s.preset ?? "last30", dateFrom: s.dateFrom, dateTo: s.dateTo,
                granularity: s.granularity || null, states: s.states || [...COUNTED],
                filters: { ...emptyFilters(), ...(s.filters || {}) }, search: s.search || "",
                breakdown: s.breakdown || "note_types",
                ...(s.lang && s.odooLang === this.odooLang ? { lang: s.lang } : {}),
            };
        } catch { return {}; }
    }
    _saveSession() {
        const s = this.state;
        try {
            window.sessionStorage.setItem(STATE_KEY, JSON.stringify({
                preset: s.preset, dateFrom: s.dateFrom, dateTo: s.dateTo, granularity: s.granularity,
                states: s.states, filters: s.filters, search: s.search, breakdown: s.breakdown,
                lang: s.lang, odooLang: this.odooLang,
            }));
        } catch { /* ignore */ }
    }
    _loadViews() {
        try { return JSON.parse(window.localStorage.getItem(VIEWS_KEY) || "[]"); } catch { return []; }
    }
    _saveViews() {
        try { window.localStorage.setItem(VIEWS_KEY, JSON.stringify(this.state.views)); } catch { /* ignore */ }
    }

    // ------------------------------------------------------------------
    // data
    // ------------------------------------------------------------------
    get data() { return this.state.data || {}; }
    get k() { return this.data.kpis || {}; }
    get kp() { return this.data.kpis_prev || {}; }
    get opts() { return this.data.options || {}; }

    async load(silent = false) {
        if (!silent) this.state.loading = true;
        this._saveSession();
        try {
            const data = await this.orm.call("mm.violation.dashboard", "get_dashboard_data", [{
                date_from: this.state.dateFrom,
                date_to: this.state.dateTo,
                granularity: this.state.granularity,
                states: this.state.states,
                search: this.state.search,
                lang: this.state.lang,
                ...this.state.filters,
            }]);
            this.state.data = data;
            this.state.updatedAt = new Date();
        } catch (e) {
            this.notification.add(e.data?.message || e.message || "Load failed", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    // ------------------------------------------------------------------
    // toolbar
    // ------------------------------------------------------------------
    toggleLang() {
        this.state.lang = this.state.lang === "ar" ? "en" : "ar";
        this._saveSession();
        this.load();  // branch / type names come translated from the server
    }
    togglePeriod() {
        this.state.customFrom = this.state.dateFrom;
        this.state.customTo = this.state.dateTo;
        this.state.periodOpen = !this.state.periodOpen;
    }
    setPreset(key) {
        const [a, b] = presetRange(key);
        Object.assign(this.state, { preset: key, dateFrom: ymd(a), dateTo: ymd(b), periodOpen: false });
        this.load();
    }
    applyCustom() {
        let a = this.state.customFrom, b = this.state.customTo;
        if (!a || !b) return;
        if (a > b) [a, b] = [b, a];
        Object.assign(this.state, { preset: null, dateFrom: a, dateTo: b, periodOpen: false });
        this.load();
    }
    setGranularity(g) { this.state.granularity = g; this.load(); }
    get statusMode() {
        const s = this.state.states;
        if (sameSet(s, COUNTED)) return "counted";
        if (sameSet(s, PIPELINE)) return "pipeline";
        if (!s.length) return "all";
        return "custom";
    }
    setStatusMode(m) {
        this.state.states = m === "counted" ? [...COUNTED] : m === "pipeline" ? [...PIPELINE] : [];
        this.load();
    }
    // typing only edits the box; Enter runs the search
    onSearchInput(ev) { this.state.searchInput = ev.target.value; }
    onSearchKeydown(ev) {
        if (ev.key !== "Enter") return;
        this.state.search = (this.state.searchInput || "").trim();
        this.load();
    }
    clearSearch() {
        this.state.searchInput = "";
        if (this.state.search) { this.state.search = ""; this.load(); }
    }
    toggleFullscreen() {
        const el = this.rootRef.el;
        if (!document.fullscreenElement) el?.requestFullscreen?.();
        else document.exitFullscreen?.();
    }
    openReport() { this.action.doAction("mm_branch_violation.mm_violation_report_wizard_action"); }
    openScorecard() { this.action.doAction("mm_branch_violation.mm_performance_scorecard_action"); }
    newViolation() {
        this.action.doAction({
            type: "ir.actions.act_window", res_model: "mm.branch.violation",
            views: [[false, "form"]], target: "current",
        });
    }

    // ------------------------------------------------------------------
    // filters: chips, cross-filtering, drawer, saved views
    // ------------------------------------------------------------------
    _nameOf(list, id) { return (list || []).find(x => x.id === id)?.name || `#${id}`; }
    get chips() {
        const f = this.state.filters, o = this.opts, out = [];
        const add = (key, value, label, group) => out.push({ key, value, label, group, id: `${key}:${value}` });
        f.branch_ids.forEach(id => add("branch_ids", id, this._nameOf(o.branches, id), this.t("branch")));
        f.employee_ids.forEach(id => add("employee_ids", id, this._nameOf(o.employees, id), this.t("employee")));
        f.type_ids.forEach(id => add("type_ids", id, this._nameOf(o.types, id), this.t("type")));
        f.source_ids.forEach(id => add("source_ids", id, this._nameOf(o.sources, id), this.L.bk.sources));
        f.job_ids.forEach(id => add("job_ids", id, this._nameOf(o.jobs, id), this.L.bk.jobs));
        f.note_types.forEach(k => add("note_types", k, this.ntLabel(k), this.t("noteTypes")));
        f.departments.forEach(k => add("departments", k, this.dpLabel(k), this.t("departments")));
        f.response_states.forEach(k => add("response_states", k, this.rsLabel(k), this.t("responseStates")));
        if (f.complaints_only) add("complaints_only", true, this.t("complaintsOnly"), "");
        if (f.repeat_only) add("repeat_only", true, this.t("repeatOnly"), "");
        if (this.statusMode === "custom") {
            this.state.states.forEach(k => add("states", k, this.stLabel(k), this.t("status")));
        }
        return out;
    }
    get filterCount() { return this.chips.length; }
    removeChip(chip) {
        if (chip.key === "states") {
            this.state.states = this.state.states.filter(s => s !== chip.value);
            if (!this.state.states.length) this.state.states = [...COUNTED];
        } else if (typeof chip.value === "boolean") {
            this.state.filters[chip.key] = false;
        } else {
            this.state.filters[chip.key] = this.state.filters[chip.key].filter(v => v !== chip.value);
        }
        this.load();
    }
    clearAll() {
        this.state.filters = emptyFilters();
        this.state.states = [...COUNTED];
        this.state.search = "";
        this.state.searchInput = "";
        this.load();
    }
    /** Cross-filter: click a chart element -> toggle that value. */
    toggleFilter(key, value) {
        if (value === undefined || value === null || value === false) return;
        const list = this.state.filters[key];
        const i = list.indexOf(value);
        if (i >= 0) list.splice(i, 1); else list.push(value);
        this.load();
    }
    isActive(key, value) { return this.state.filters[key].includes(value); }
    filterCell(branchId, typeId) {
        const f = this.state.filters;
        if (!f.branch_ids.includes(branchId)) f.branch_ids.push(branchId);
        if (typeId && !f.type_ids.includes(typeId)) f.type_ids.push(typeId);
        this.load();
    }
    setStates(states) { this.state.states = states; this.load(); }
    toggleRepeat() { this.state.filters.repeat_only = !this.state.filters.repeat_only; this.load(); }

    // drawer
    openDrawer() {
        this.state.draft = JSON.parse(JSON.stringify(this.state.filters));
        this.state.draftStates = [...this.state.states];
        this.state.drawerOpen = true;
        this.state.viewsOpen = false;
    }
    closeDrawer() { this.state.drawerOpen = false; }
    applyDrawer() {
        this.state.filters = this.state.draft;
        this.state.states = this.state.draftStates.length ? this.state.draftStates : [];
        this.state.drawerOpen = false;
        this.load();
    }
    resetDrawer() {
        this.state.draft = emptyFilters();
        this.state.draftStates = [...COUNTED];
    }
    draftToggle(key, value) {
        const list = this.state.draft[key];
        const i = list.indexOf(value);
        if (i >= 0) list.splice(i, 1); else list.push(value);
    }
    draftHas(key, value) { return this.state.draft[key].includes(value); }
    draftStateToggle(k) {
        const l = this.state.draftStates, i = l.indexOf(k);
        if (i >= 0) l.splice(i, 1); else l.push(k);
    }
    draftBool(key) { this.state.draft[key] = !this.state.draft[key]; }
    onDrawerQ(key, ev) { this.state.drawerQ[key] = ev.target.value; }
    drawerList(key) {
        const q = (this.state.drawerQ[key] || "").toLowerCase().trim();
        const list = this.opts[key] || [];
        const field = { branches: "branch_ids", employees: "employee_ids", types: "type_ids", sources: "source_ids", jobs: "job_ids" }[key];
        // picked first, then matches
        const picked = list.filter(x => this.state.draft[field].includes(x.id));
        const rest = list.filter(x => !this.state.draft[field].includes(x.id)
            && (!q || `${x.name} ${x.number || ""} ${x.branch || ""}`.toLowerCase().includes(q)));
        return [...picked, ...rest.slice(0, key === "employees" ? 60 : 200)];
    }
    get draftCount() {
        const d = this.state.draft;
        if (!d) return 0;
        return ["branch_ids", "employee_ids", "type_ids", "source_ids", "job_ids", "note_types", "departments", "response_states"]
            .reduce((n, k) => n + d[k].length, 0) + (d.complaints_only ? 1 : 0) + (d.repeat_only ? 1 : 0);
    }

    // saved views (per browser)
    toggleViews() { this.state.viewsOpen = !this.state.viewsOpen; this.state.viewName = ""; }
    onViewName(ev) { this.state.viewName = ev.target.value; }
    saveView() {
        const name = (this.state.viewName || "").trim();
        if (!name) return;
        const s = this.state;
        const view = {
            name, preset: s.preset, dateFrom: s.dateFrom, dateTo: s.dateTo,
            states: [...s.states], filters: JSON.parse(JSON.stringify(s.filters)), search: s.search,
        };
        s.views = [...s.views.filter(v => v.name !== name), view];
        s.viewName = "";
        this._saveViews();
    }
    applyView(v) {
        const s = this.state;
        s.preset = v.preset;
        if (v.preset) {
            const [a, b] = presetRange(v.preset);
            s.dateFrom = ymd(a); s.dateTo = ymd(b);
        } else {
            s.dateFrom = v.dateFrom; s.dateTo = v.dateTo;
        }
        s.states = [...(v.states || COUNTED)];
        s.filters = { ...emptyFilters(), ...JSON.parse(JSON.stringify(v.filters || {})) };
        s.search = v.search || "";
        s.searchInput = s.search;
        s.viewsOpen = false;
        this.load();
    }
    deleteView(v) {
        this.state.views = this.state.views.filter(x => x.name !== v.name);
        this._saveViews();
    }

    // ------------------------------------------------------------------
    // drill-down
    // ------------------------------------------------------------------
    openList(extra = [], name = null, base = "domain") {
        const domain = [...(this.data[base] || []), ...extra];
        this.action.doAction({
            type: "ir.actions.act_window",
            name: name || this.t("title"),
            res_model: "mm.branch.violation",
            domain,
            views: [[false, "list"], [false, "pivot"], [false, "graph"], [false, "form"]],
            target: "current",
            context: { create: false },
        });
    }
    openRecord(id) {
        this.action.doAction({
            type: "ir.actions.act_window", res_model: "mm.branch.violation", res_id: id,
            views: [[false, "form"]], target: "current",
        });
    }
    openAction(kind) {
        const doms = {
            to_review: [["state", "=", "under_review"]],
            stale_drafts: [["state", "=", "draft"]],
            overdue: [["response_overdue", "=", true]],
            awaiting: [["response_state", "=", "pending"]],
            contacted: [["response_state", "=", "contacted"]],
            pending_deductions: [["mm_deduction_status", "=", "pending"]],
        };
        const titles = {
            to_review: "toReview", stale_drafts: "staleDrafts", overdue: "overdueComplaints",
            awaiting: "awaitingResponse", contacted: "contacted", pending_deductions: "pendingDeductions",
        };
        this.openList(doms[kind], this.t(titles[kind]), "domain_open");
    }

    // ------------------------------------------------------------------
    // derived figures
    // ------------------------------------------------------------------
    delta(cur, prev, lowerBetter = true, isRate = false) {
        cur = Number(cur || 0);
        if (prev === null || prev === undefined) return null;
        prev = Number(prev || 0);
        if (isRate) {
            const d = Math.round((cur - prev) * 10) / 10;
            if (!d) return { label: "0 pp", cls: "mm-v-flat", arrow: "→" };
            const good = lowerBetter ? d < 0 : d > 0;
            return { label: `${d > 0 ? "+" : ""}${this.n(d, 1)} pp`, cls: good ? "mm-v-good" : "mm-v-bad", arrow: d > 0 ? "▲" : "▼" };
        }
        if (!prev && !cur) return { label: "0%", cls: "mm-v-flat", arrow: "→" };
        if (!prev) return { label: this.t("noPrev"), cls: "mm-v-flat", arrow: "" };
        const pct = Math.round(((cur - prev) / prev) * 100);
        if (!pct) return { label: "0%", cls: "mm-v-flat", arrow: "→" };
        const good = lowerBetter ? pct < 0 : pct > 0;
        return { label: `${pct > 0 ? "+" : ""}${this.n(pct)}%`, cls: good ? "mm-v-good" : "mm-v-bad", arrow: pct > 0 ? "▲" : "▼" };
    }
    rowDelta(r) {
        if (!r.prev && !r.count) return null;
        const d = r.count - (r.prev || 0);
        if (!d) return { label: "=", cls: "mm-v-flat" };
        return { label: `${d > 0 ? "+" : ""}${d}`, cls: d > 0 ? "mm-v-bad" : "mm-v-good" };
    }
    get actionTiles() {
        const a = this.data.actions || {};
        const tiles = [
            { key: "to_review", value: a.to_review, label: this.t("toReview"),
              sub: a.to_review ? this.t("oldest", a.to_review_days) : "", tone: a.to_review_days > 2 ? "bad" : "warn" },
            { key: "overdue", value: a.overdue, label: this.t("overdueComplaints"), sub: "", tone: "bad" },
            { key: "awaiting", value: a.awaiting, label: this.t("awaitingResponse"), sub: "", tone: "warn" },
            { key: "contacted", value: a.contacted, label: this.t("contacted"), sub: "", tone: "info" },
            { key: "stale_drafts", value: a.stale_drafts, label: this.t("staleDrafts"),
              sub: this.t("olderThan", a.stale_draft_days || 2), tone: "warn" },
            { key: "pending_deductions", value: a.pending_deductions, label: this.t("pendingDeductions"),
              sub: this.t("noCommission"), tone: "info" },
        ];
        return tiles;
    }
    get attentionCount() {
        const a = this.data.actions || {};
        return (a.to_review || 0) + (a.overdue || 0) + (a.awaiting || 0) + (a.stale_drafts || 0);
    }
    get funnel() {
        const f = this.data.funnel || {};
        const max = Math.max(1, ...STATES.map(s => f[s] || 0));
        return STATES.map(s => ({
            key: s, label: this.stLabel(s), value: f[s] || 0,
            pct: Math.round(((f[s] || 0) / max) * 100),
            active: this.state.states.length === 1 && this.state.states[0] === s,
        }));
    }
    get heat() {
        const m = this.data.matrix || { cols: [], rows: [], peak: 0 };
        const cols = [...m.cols];
        if (m.has_other) cols.push({ id: 0, name: this.t("other") });
        return {
            cols,
            rows: m.rows.map(r => ({
                ...r,
                cells: r.cells.map((v, i) => ({
                    v, typeId: cols[i]?.id,
                    style: v ? `background: rgba(208,59,59,${(0.12 + 0.78 * v / (m.peak || 1)).toFixed(2)}); color: ${v / (m.peak || 1) > 0.55 ? "#fff" : "#1c1d22"}` : "",
                })),
            })),
        };
    }
    get employees() {
        const rows = this.data.employees || [];
        const max = Math.max(1, ...rows.map(r => r.count));
        return rows.map((r, i) => ({ ...r, rank: i + 1, bar: Math.round((r.count / max) * 100), delta: this.rowDelta(r) }));
    }
    daysAgo(d) { return d ? this.t("daysAgo", d) : this.t("todayShort"); }
    get reps() { return (this.data.complaints || {}).by_rep || []; }
    get overdueList() { return (this.data.complaints || {}).overdue || []; }
    get isEmpty() { return !this.state.loading && this.data.kpis && !this.k.logged; }

    get insights() {
        const d = this.data, k = this.k, kp = this.kp, out = [];
        if (!d.kpis) return out;
        const total = k.violations || k.logged || 0;
        const branches = d.branches || [];
        if (total && kp.violations !== undefined && (kp.violations || 0) > 0) {
            const pct = Math.round(((total - kp.violations) / kp.violations) * 100);
            if (Math.abs(pct) >= 10) {
                out.push({ tone: pct > 0 ? "bad" : "good", icon: pct > 0 ? "▲" : "▼",
                    text: this.t(pct > 0 ? "insUp" : "insDown", this.n(Math.abs(pct)), this.n(kp.violations), this.n(total)) });
            }
        }
        if (branches.length >= 2 && total) {
            const b = branches[0];
            const share = Math.round((b.count / branches.reduce((s, r) => s + r.count, 0)) * 100);
            if (share >= 25) {
                out.push({ tone: "bad", icon: "◎", text: this.t("insShare", b.name, share, this.n(b.count)),
                    click: () => this.toggleFilter("branch_ids", b.id) });
            }
        }
        const rise = (d.types || []).filter(t => t.count >= 2 && t.count > (t.prev || 0) * 1.5 && t.prev > 0)
            .sort((a, b) => (b.count - b.prev) - (a.count - a.prev))[0];
        if (rise) {
            out.push({ tone: "bad", icon: "↗", text: this.t("insTypeRise", rise.name, this.n(rise.prev), this.n(rise.count)),
                click: () => this.toggleFilter("type_ids", rise.id) });
        }
        const rep = (d.employees || []).filter(e => e.same_type_repeat >= 2).sort((a, b) => b.same_type_repeat - a.same_type_repeat)[0];
        if (rep) {
            out.push({ tone: "bad", icon: "↻", text: this.t("insRepeat", rep.name, rep.top_type, rep.same_type_repeat),
                click: () => this.toggleFilter("employee_ids", rep.id) });
        }
        const a = d.actions || {};
        if (a.overdue) {
            out.push({ tone: "bad", icon: "⏰", text: this.t("insOverdue", this.n(a.overdue)), click: () => this.openAction("overdue") });
        }
        if (a.to_review) {
            out.push({ tone: "warn", icon: "⏳", text: this.t("insBacklog", this.n(a.to_review), this.n(a.to_review_days)),
                click: () => this.openAction("to_review") });
        }
        const types = d.types || [];
        if (types.length >= 4 && total >= 5) {
            let acc = 0, kTypes = 0;
            for (const t of types) { acc += t.count; kTypes++; if (acc / total >= 0.8) break; }
            if (kTypes <= Math.ceil(types.length / 2)) {
                out.push({ tone: "info", icon: "★", text: this.t("insPareto", kTypes, Math.round((acc / total) * 100)) });
            }
        }
        const wd = d.weekdays || [];
        const wdTotal = wd.reduce((s, v) => s + v, 0);
        if (wdTotal >= 7) {
            const i = wd.indexOf(Math.max(...wd));
            const share = Math.round((wd[i] / wdTotal) * 100);
            if (share >= 22) out.push({ tone: "info", icon: "📅", text: this.t("insWeekday", this.L.wdLong[i], share) });
        }
        const allBranches = (this.opts.branches || []).length;
        const clean = this.state.filters.branch_ids.length ? 0 : allBranches - branches.length;
        if (clean > 0 && total) out.push({ tone: "good", icon: "✓", text: this.t("insClean", this.n(clean)) });
        return out.slice(0, 6);
    }

    // ------------------------------------------------------------------
    // charts
    // ------------------------------------------------------------------
    destroyCharts() {
        this._charts.forEach(c => { try { c.destroy(); } catch { /* noop */ } });
        this._charts = [];
    }
    _mk(key, options) {
        const el = this.refs[key]?.el;
        if (!el || typeof window.ApexCharts === "undefined") return;
        el.innerHTML = "";
        el.style.direction = "ltr";  // ApexCharts breaks under RTL
        const base = {
            chart: { fontFamily: "IBM Plex Sans Arabic, system-ui, sans-serif", toolbar: { show: false },
                     animations: { enabled: true, speed: 350 }, ...(options.chart || {}) },
            grid: { borderColor: C.grid, strokeDashArray: 3 },
            dataLabels: { enabled: false },
            tooltip: { theme: "light" },
        };
        try {
            const c = new window.ApexCharts(el, { ...base, ...options, chart: base.chart });
            c.render();
            this._charts.push(c);
        } catch (e) {
            console.warn("mm violation dashboard chart", key, e);
        }
    }
    _spark(key, data, color) {
        this._mk(key, {
            chart: { type: "area", height: 64, sparkline: { enabled: true } },
            series: [{ name: "", data }],
            colors: [color],
            stroke: { width: 2, curve: "smooth" },
            fill: { type: "gradient", gradient: { opacityFrom: 0.35, opacityTo: 0.02 } },
            tooltip: { enabled: false },
        });
    }
    renderCharts() {
        this.destroyCharts();
        const d = this.state.data;
        if (this.state.loading || !d || !d.trend) return;
        const tr = d.trend;
        const labels = tr.labels.map(l => this.bucketLabel(l));

        this._spark("sparkV", tr.total, C.accent);
        this._spark("sparkP", tr.points, C.bad);
        this._spark("sparkC", tr.complaints, "#eb6834");

        // complaint handling gauge: resolved % + on time %
        const k = d.kpis;
        this._mk("gauge", {
            chart: { type: "radialBar", height: 200 },
            series: [k.resolved_pct ?? 0, k.on_time_pct ?? 0],
            labels: [this.t("resolvedPct"), this.t("onTimePct")],
            colors: [C.good, C.accent],
            plotOptions: { radialBar: {
                hollow: { size: "38%" }, track: { background: "#f1f2f6" },
                dataLabels: { name: { fontSize: "11px" }, value: { fontSize: "15px", fontWeight: 700, formatter: v => `${Math.round(v)}%` },
                              total: { show: false } },
            } },
            stroke: { lineCap: "round" },
            legend: { show: true, position: "bottom", fontWeight: 600, fontSize: "12px",
                      formatter: (name, o) => `${Math.round(o.w.globals.series[o.seriesIndex])}% ${name}` },
        });

        // trend: violations per bucket (category split in the tooltip),
        // previous period dashed on the same axis, points on the right axis
        const cats = NOTE_TYPES.filter(nt => tr.by_note_type[nt]);
        const maxCount = Math.max(1, ...tr.total, ...tr.prev_total);
        this._mk("trend", {
            chart: { type: "line", height: 320 },
            series: [
                { name: this.t("kViolations"), type: "column", data: tr.total },
                { name: this.t("prevPeriod"), type: "line", data: tr.prev_total },
                { name: this.t("pointsSeries"), type: "line", data: tr.points },
            ],
            colors: [C.accent, "#9aa1ad", C.bad],
            labels,
            stroke: { width: [0, 2, 3], curve: "smooth", dashArray: [0, 6, 0] },
            fill: { type: ["gradient", "solid", "solid"], gradient: { shade: "light", type: "vertical", opacityFrom: 1, opacityTo: 0.75 } },
            plotOptions: { bar: { columnWidth: labels.length > 20 ? "70%" : "45%", borderRadius: 5, borderRadiusApplication: "end" } },
            markers: { size: [0, 0, 3] },
            yaxis: [
                { seriesName: this.t("kViolations"), min: 0, max: maxCount, forceNiceScale: true, labels: { formatter: v => this.n(v) } },
                { seriesName: this.t("kViolations"), show: false, min: 0, max: maxCount, forceNiceScale: true },
                { opposite: true, min: 0, forceNiceScale: true, title: { text: this.t("pointsSeries"), style: { color: C.bad, fontWeight: 600 } },
                  labels: { formatter: v => this.n(v), style: { colors: C.bad } } },
            ],
            legend: { position: "top", horizontalAlign: "left", fontWeight: 600 },
            xaxis: { labels: { rotate: -45, hideOverlappingLabels: true } },
            tooltip: { shared: true, intersect: false, custom: ({ dataPointIndex: i }) => {
                const rows = cats.filter(nt => tr.by_note_type[nt][i]).map(nt =>
                    `<div><span class="mm-v-dot" style="background:${NOTE_COLORS[nt]}"></span>${this.ntLabel(nt)}: <b>${this.n(tr.by_note_type[nt][i])}</b></div>`).join("");
                return `<div class="mm-v-tip"><b>${labels[i]}</b>`
                    + `<div>${this.t("kViolations")}: <b>${this.n(tr.total[i])}</b></div>${rows}`
                    + `<div class="mm-v-tip-sep">${this.t("prevPeriod")}: <b>${this.n(tr.prev_total[i])}</b></div>`
                    + `<div>${this.t("pointsSeries")}: <b>${this.n(tr.points[i], 1)}</b></div></div>`;
            } },
        });

        // branches: horizontal bars, click = filter
        const br = d.branches.slice(0, 15);
        const maxB = Math.max(1, ...br.map(b => b.count));
        this._mk("branch", {
            chart: { type: "bar", height: Math.max(220, br.length * 34 + 40),
                     events: { dataPointSelection: (e, ctx, cfg) => { const b = br[cfg.dataPointIndex]; if (b) this.toggleFilter("branch_ids", b.id); } } },
            series: [{ name: this.t("count"), data: br.map(b => b.count) }],
            xaxis: { categories: br.map(b => b.name), max: Math.ceil(maxB * 1.15), tickAmount: Math.min(Math.ceil(maxB * 1.15), 6),
                     labels: { formatter: v => this.n(v) } },
            colors: br.map(b => {
                const r = b.count / maxB;
                return r > 0.66 ? C.bad : r > 0.33 ? C.warn : C.good;
            }),
            plotOptions: { bar: { horizontal: true, distributed: true, borderRadius: 5, barHeight: "62%",
                                  dataLabels: { position: "top" } } },
            dataLabels: { enabled: true, offsetX: 22, style: { colors: ["#1c1d22"], fontWeight: 700 }, formatter: v => this.n(v) },
            legend: { show: false },
            states: { active: { filter: { type: "none" } } },
            tooltip: { custom: ({ dataPointIndex }) => {
                const b = br[dataPointIndex];
                return `<div class="mm-v-tip"><b>${b.name}</b><br/>${this.t("count")}: ${this.n(b.count)}<br/>`
                    + `${this.t("points")}: ${this.n(b.points, 1)}<br/>${this.t("kEmployees")}: ${this.n(b.employees)}`
                    + (b.per_employee !== null ? `<br/>${this.n(b.per_employee, 2)} ${this.t("perEmployee")} (${this.n(b.headcount)} ${this.t("staff")})` : "")
                    + (b.top_type ? `<br/>${this.t("topType")}: ${b.top_type}` : "") + `</div>`;
            } },
        });

        // breakdown donut (category / entered by / source / job)
        const bk = this.state.breakdown;
        const rows = d[bk] || [];
        const isSel = bk === "note_types" || bk === "departments";
        const bLabels = rows.map(r => bk === "note_types" ? this.ntLabel(r.key) : bk === "departments" ? this.dpLabel(r.key) : r.name);
        const filterKey = { note_types: "note_types", departments: "departments", sources: "source_ids", jobs: "job_ids" }[bk];
        this._mk("breakdown", {
            chart: { type: "donut", height: 290,
                     events: { dataPointSelection: (e, ctx, cfg) => { const r = rows[cfg.dataPointIndex]; if (r) this.toggleFilter(filterKey, isSel ? r.key : r.id); } } },
            series: rows.map(r => r.count),
            labels: bLabels,
            colors: bk === "note_types" ? rows.map(r => NOTE_COLORS[r.key] || C.ink2) : SERIES,
            legend: { position: "bottom", fontWeight: 600 },
            dataLabels: { enabled: true, formatter: v => `${Math.round(v)}%`, dropShadow: { enabled: false } },
            plotOptions: { pie: { donut: { size: "62%", labels: { show: true,
                total: { show: true, label: this.t("total"), formatter: () => this.n(rows.reduce((s, r) => s + r.count, 0)) } } } } },
            stroke: { width: 2 },
        });

        // Pareto of violation types
        const types = d.types.slice(0, 10);
        const tTotal = d.types.reduce((s, t) => s + t.count, 0) || 1;
        let acc = 0;
        const cum = types.map(t => { acc += t.count; return Math.round((acc / tTotal) * 100); });
        this._mk("pareto", {
            chart: { type: "line", height: 320,
                     events: { dataPointSelection: (e, ctx, cfg) => { const t = types[cfg.dataPointIndex]; if (t && cfg.seriesIndex === 0) this.toggleFilter("type_ids", t.id); } } },
            series: [
                { name: this.t("count"), type: "column", data: types.map(t => t.count) },
                { name: this.t("cumulative"), type: "line", data: cum },
            ],
            labels: types.map(t => (t.name.length > 26 ? t.name.slice(0, 25) + "…" : t.name)),
            colors: [C.accent, C.warn],
            stroke: { width: [0, 3], curve: "smooth" },
            markers: { size: [0, 4] },
            plotOptions: { bar: { columnWidth: "55%", borderRadius: 5, borderRadiusApplication: "end" } },
            yaxis: [
                { labels: { formatter: v => this.n(v) }, min: 0, forceNiceScale: true },
                { opposite: true, max: 100, min: 0, labels: { formatter: v => `${Math.round(v)}%` } },
            ],
            xaxis: { labels: { rotate: -35, trim: true, style: { fontSize: "11px" } } },
            legend: { position: "top", horizontalAlign: "left", fontWeight: 600 },
            tooltip: { shared: true, intersect: false, x: { formatter: (v, o) => types[o.dataPointIndex]?.name || v } },
        });

        // weekday
        const wd = d.weekdays;
        const maxW = Math.max(...wd);
        this._mk("weekday", {
            chart: { type: "bar", height: 310 },
            series: [{ name: this.t("count"), data: wd }],
            xaxis: { categories: this.L.wd },
            colors: wd.map(v => (v === maxW && v > 0 ? C.bad : "#c9bff0")),
            plotOptions: { bar: { distributed: true, borderRadius: 5, columnWidth: "55%" } },
            legend: { show: false },
            yaxis: { labels: { formatter: v => this.n(v) } },
        });

        // complaint response states
        const rs = d.complaints.states || {};
        const rKeys = RESPONSE_STATES.filter(s => rs[s]);
        this._mk("response", {
            chart: { type: "donut", height: 250,
                     events: { dataPointSelection: (e, ctx, cfg) => this.toggleFilter("response_states", rKeys[cfg.dataPointIndex]) } },
            series: rKeys.map(s => rs[s]),
            labels: rKeys.map(s => this.rsLabel(s)),
            colors: rKeys.map(s => RESPONSE_COLORS[s]),
            legend: { position: "bottom", fontWeight: 600 },
            dataLabels: { enabled: true, formatter: (v, o) => this.n(o.w.globals.series[o.seriesIndex]), dropShadow: { enabled: false } },
            plotOptions: { pie: { donut: { size: "60%", labels: { show: true,
                total: { show: true, label: this.t("complaintsCol"), formatter: () => this.n(k.complaints) } } } } },
        });
    }
    setBreakdown(b) { this.state.breakdown = b; this._saveSession(); }
}

registry.category("actions").add("mm_branch_violation.violation_dashboard", ViolationDashboard);
