import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  AppBar,
  Box,
  Button,
  CircularProgress,
  Container,
  MenuItem,
  Paper,
  Stack,
  Tab,
  Tabs,
  TextField,
  Toolbar,
  Typography,
} from "@mui/material";
import {
  api,
  type Page,
  type Row,
  type Stats,
  type FailureSummary as Summary,
} from "./api";
import SignIn from "./SignIn";
import FailureSummary from "./FailureSummary";
import { ActivityTable, EvidenceDialog, Pager } from "./ActivityViews";

const tabs = [
  { key: "overview", title: "📊 Overview" },
  { key: "inbox", title: "📨 Inbox" },
  { key: "listings", title: "🏘️ Listings" },
  { key: "applications", title: "✅ Applications" },
  { key: "evidence", title: "🖼️ Evidence" },
];
const day = (date: Date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;

export default function App() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<{ authenticated: boolean }>("/auth/session")
      .then((data) => setAuthenticated(data.authenticated))
      .catch((reason) => {
        setError(reason.message);
        setAuthenticated(false);
      });
    const expired = () => setAuthenticated(false);
    window.addEventListener("session-expired", expired);
    return () => window.removeEventListener("session-expired", expired);
  }, []);
  if (authenticated === null)
    return (
      <Box display="grid" minHeight="100vh" sx={{ placeItems: "center" }}>
        <CircularProgress />
      </Box>
    );
  if (!authenticated)
    return (
      <>
        <SignIn
          onSignedIn={() => {
            setError("");
            setAuthenticated(true);
          }}
        />
        {error && <Alert severity="error">{error}</Alert>}
      </>
    );
  return (
    <Dashboard
      onLogout={async () => {
        await api("/auth/logout", { method: "POST" });
        setAuthenticated(false);
      }}
    />
  );
}

function Dashboard({ onLogout }: { onLogout: () => Promise<void> }) {
  const now = new Date();
  const [tab, setTab] = useState(0);
  const [from, setFrom] = useState(
    day(new Date(now.getFullYear(), now.getMonth(), 1)),
  );
  const [to, setTo] = useState(
    day(new Date(now.getFullYear(), now.getMonth() + 1, 1)),
  );
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(25);
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [stats, setStats] = useState<Stats | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [data, setData] = useState<Page<Row>>({
    items: [],
    total: 0,
    page: 0,
    page_size: 25,
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [evidence, setEvidence] = useState<Row | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const range = useMemo(
    () =>
      new URLSearchParams({
        from: `${from}T00:00:00Z`,
        to: `${to}T00:00:00Z`,
      }).toString(),
    [from, to],
  );
  const kind = tabs[tab].key;
  useEffect(() => {
    const timer = setTimeout(() => {
      setQuery(search);
      setPage(0);
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);
  useEffect(() => {
    let active = true;
    Promise.all([
      api<Stats>(`/stats?${range}`),
      api<Summary>(`/failures?${range}&limit=1`),
    ])
      .then(([nextStats, nextSummary]) => {
        if (active) {
          setStats(nextStats);
          setSummary(nextSummary);
        }
      })
      .catch((reason) => {
        if (active) setError(reason.message);
      });
    api<{ problems: string[] }>("/pipeline-status")
      .then((result) => {
        if (active) setProblems(result.problems);
      })
      .catch((reason) => {
        if (active) setError(reason.message);
      });
    return () => {
      active = false;
    };
  }, [range, refresh]);
  useEffect(() => {
    if (kind === "overview") return;
    let active = true;
    setLoading(true);
    setError("");
    const params = new URLSearchParams({
      kind,
      page: String(page),
      page_size: String(size),
      search: query,
    });
    if (status) params.set("status", status);
    api<Page<Row>>(`/activity?${range}&${params}`)
      .then((result) => {
        if (active) setData(result);
      })
      .catch((reason) => {
        if (active) setError(reason.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [kind, page, size, query, status, range, refresh]);
  const providers = stats?.sources.provider_listings || {};
  const total = (source: string) =>
    Object.values(stats?.sources[source] || {}).reduce((a, b) => a + b, 0);
  const failures = Object.values(stats?.failures || {}).reduce(
    (a, b) => a + b,
    0,
  );
  const badges: Record<string, number> = {
    inbox:
      (stats?.failures.emails || 0) + (stats?.failures.stekkies_links || 0),
    listings: stats?.failures.provider_listings || 0,
  };
  function changeTab(next: number) {
    setTab(next);
    setPage(0);
    setStatus("");
    setSearch("");
    setQuery("");
    setData({ items: [], total: 0, page: 0, page_size: size });
  }
  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "#f5f7f6" }}>
      <AppBar
        position="sticky"
        elevation={0}
        color="transparent"
        sx={{ bgcolor: "white", borderBottom: "1px solid #e0e8e3" }}
      >
        <Toolbar>
          <Typography fontWeight={800} variant="h6" flexGrow={1}>
            🏡 House Bot
          </Typography>
          <Button onClick={() => setRefresh((n) => n + 1)}>Refresh</Button>
          <Button
            onClick={() =>
              onLogout().catch((reason) => setError(reason.message))
            }
          >
            Sign out
          </Button>
        </Toolbar>
      </AppBar>
      <Container maxWidth="xl" sx={{ py: 4 }}>
        <Stack
          direction={{ xs: "column", md: "row" }}
          justifyContent="space-between"
          gap={2}
          mb={3}
        >
          <Box>
            <Typography variant="h4" fontWeight={800}>
              Your housing pipeline
            </Typography>
            <Typography color="text.secondary" mt={0.5}>
              From new matches to confirmed applications.
            </Typography>
          </Box>
          <Stack direction="row" gap={1}>
            <TextField
              label="From"
              type="date"
              size="small"
              value={from}
              onChange={(e) => {
                setFrom(e.target.value);
                setPage(0);
              }}
              slotProps={{ inputLabel: { shrink: true } }}
            />
            <TextField
              label="Until (exclusive)"
              type="date"
              size="small"
              value={to}
              onChange={(e) => {
                setTo(e.target.value);
                setPage(0);
              }}
              slotProps={{ inputLabel: { shrink: true } }}
            />
          </Stack>
        </Stack>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        {problems.length > 0 && (
          <Alert severity="warning" sx={{ mb: 2 }}>
            Submissions paused: {problems.join("; ")}. Update the applicant
            names in values.yaml, then refresh.
          </Alert>
        )}
        <Paper variant="outlined" sx={{ borderRadius: 3, mb: 3 }}>
          <Tabs
            value={tab}
            onChange={(_, next) => changeTab(next)}
            variant="scrollable"
            scrollButtons="auto"
          >
            {tabs.map((item) => (
              <Tab
                key={item.key}
                label={`${item.title}${badges[item.key] ? ` · ${badges[item.key]} errors` : ""}`}
              />
            ))}
          </Tabs>
        </Paper>
        {kind === "overview" ? (
          <Stack gap={3}>
            <Stack direction="row" flexWrap="wrap" gap={2}>
              {[
                ["📨 Emails", total("emails")],
                ["🔗 Matches", total("stekkies_links")],
                ["🏘️ Listings", total("provider_listings")],
                ["✅ Submitted", providers.submitted || 0],
                [
                  "⏳ Queued",
                  (providers.pending || 0) + (providers.processing || 0),
                ],
                ["⚠️ Errors", failures],
              ].map(([name, value]) => (
                <Paper
                  key={name}
                  variant="outlined"
                  sx={{ p: 3, borderRadius: 3, flex: "1 1 160px" }}
                >
                  <Typography color="text.secondary">{name}</Typography>
                  <Typography variant="h3" fontWeight={700} mt={1}>
                    {value}
                  </Typography>
                </Paper>
              ))}
            </Stack>
            <Alert severity="info">
              Applications are counted only after provider confirmation. Open
              Evidence to inspect screenshots from successful or failed
              attempts.
            </Alert>
            <FailureSummary data={summary} range={range} />
          </Stack>
        ) : (
          <Stack gap={3}>
            <Paper
              variant="outlined"
              sx={{ borderRadius: 3, overflow: "hidden" }}
            >
              <Stack
                direction={{ xs: "column", sm: "row" }}
                gap={2}
                p={2.5}
                alignItems={{ sm: "center" }}
              >
                <Typography variant="h6" fontWeight={700} flexGrow={1}>
                  {tabs[tab].title}{" "}
                  <Typography component="span" color="text.secondary">
                    ({data.total})
                  </Typography>
                </Typography>
                <TextField
                  size="small"
                  label={
                    kind === "inbox"
                      ? "Search email"
                      : "Search listing or website"
                  }
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
                {kind !== "applications" && (
                  <TextField
                    select
                    size="small"
                    label="Status"
                    value={status}
                    sx={{ minWidth: 150 }}
                    onChange={(e) => {
                      setStatus(e.target.value);
                      setPage(0);
                    }}
                  >
                    <MenuItem value="">All statuses</MenuItem>
                    {(kind === "inbox"
                      ? ["pending", "processing", "processed", "error"]
                      : ["pending", "processing", "submitted", "error"]
                    ).map((value) => (
                      <MenuItem key={value} value={value}>
                        {value}
                      </MenuItem>
                    ))}
                  </TextField>
                )}
              </Stack>
              {loading ? (
                <Box textAlign="center" py={8}>
                  <CircularProgress />
                </Box>
              ) : (
                <ActivityTable
                  kind={kind}
                  rows={data.items}
                  onEvidence={setEvidence}
                />
              )}
              <Pager
                total={data.total}
                page={page}
                size={size}
                onPage={setPage}
                onSize={(n) => {
                  setSize(n);
                  setPage(0);
                }}
              />
            </Paper>
            {(kind === "inbox" || kind === "listings") && (
              <FailureSummary
                data={summary}
                range={range}
                inbox={kind === "inbox"}
              />
            )}
          </Stack>
        )}
      </Container>
      {evidence && (
        <EvidenceDialog
          key={evidence.id}
          row={evidence}
          onClose={() => setEvidence(null)}
        />
      )}
    </Box>
  );
}
