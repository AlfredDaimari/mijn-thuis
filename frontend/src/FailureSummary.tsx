import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Dialog,
  DialogContent,
  DialogTitle,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import {
  api,
  dateTime,
  label,
  type Failure,
  type FailureSummary as Summary,
  type Page,
} from "./api";
import { FullLink, Pager } from "./ActivityViews";

type Filter = { source: string; website: string; category: string };

function FailureDetails({
  filter,
  range,
  onClose,
}: {
  filter: Filter;
  range: string;
  onClose: () => void;
}) {
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(25);
  const [data, setData] = useState<Page<Failure>>({
    items: [],
    total: 0,
    page: 0,
    page_size: 25,
  });
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    const query = new URLSearchParams({
      source: filter.source,
      website: filter.website,
      category: filter.category,
      page: String(page),
      page_size: String(size),
    });
    api<Page<Failure>>(`/failure-page?${range}&${query}`)
      .then((result) => {
        if (active) setData(result);
      })
      .catch((reason) => {
        if (active) setError(reason.message);
      });
    return () => {
      active = false;
    };
  }, [filter, range, page, size]);
  return (
    <Dialog open onClose={onClose} maxWidth="lg" fullWidth>
      <DialogTitle>
        <Stack direction="row" justifyContent="space-between">
          <span>
            ⚠️ {filter.website || label(filter.source)} ·{" "}
            {label(filter.category) || "all failures"}
          </span>
          <Button onClick={onClose}>Close</Button>
        </Stack>
      </DialogTitle>
      <DialogContent>
        {error && <Alert severity="error">{error}</Alert>}
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Full listing URL / email</TableCell>
                <TableCell>Class</TableCell>
                <TableCell>Exact reason</TableCell>
                <TableCell>Failed</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.items.map((row) => (
                <TableRow key={row.source_id}>
                  <TableCell sx={{ maxWidth: 400 }}>
                    {row.source_url ? (
                      <FullLink href={row.source_url} />
                    ) : (
                      row.label
                    )}
                  </TableCell>
                  <TableCell>{label(row.error_class)}</TableCell>
                  <TableCell sx={{ maxWidth: 400, overflowWrap: "anywhere" }}>
                    {row.processing_error}
                  </TableCell>
                  <TableCell>{dateTime(row.processed_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
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
      </DialogContent>
    </Dialog>
  );
}

export default function FailureSummary({
  data,
  range,
  inbox = false,
}: {
  data: Summary | null;
  range: string;
  inbox?: boolean;
}) {
  const [filter, setFilter] = useState<Filter | null>(null);
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(25);
  const websites =
    data?.summary.by_url.filter(
      (row) => row.source_table === "provider_listings",
    ) || [];
  const classes =
    data?.summary.by_class.filter((row) =>
      ["emails", "stekkies_links"].includes(row.source_table),
    ) || [];
  return (
    <>
      <Paper variant="outlined" sx={{ borderRadius: 3, overflow: "hidden" }}>
        <Typography variant="h6" fontWeight={700} p={3}>
          {inbox ? "⚠️ Inbox failures" : "⚠️ Failures by website"}
        </Typography>
        {inbox ? (
          <Stack direction="row" gap={1} flexWrap="wrap" px={3} pb={3}>
            {classes.length ? (
              classes.map((row) => (
                <Button
                  key={`${row.source_table}-${row.error_class}`}
                  variant="outlined"
                  size="small"
                  onClick={() =>
                    setFilter({
                      source: row.source_table,
                      website: "",
                      category: row.error_class,
                    })
                  }
                >
                  {row.source_table === "emails" ? "Email" : "Link"} ·{" "}
                  {label(row.error_class)} · {row.count}
                </Button>
              ))
            ) : (
              <Typography color="text.secondary">
                No inbox failures in this period.
              </Typography>
            )}
          </Stack>
        ) : (
          <>
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Website</TableCell>
                    <TableCell>Error classes · counts</TableCell>
                    <TableCell>Total</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {websites.slice(page * size, (page + 1) * size).map((row) => (
                    <TableRow key={row.source_url}>
                      <TableCell>{row.source_url}</TableCell>
                      <TableCell>
                        <Stack direction="row" gap={0.75} flexWrap="wrap">
                          {row.by_class.map((category) => (
                            <Button
                              key={category.error_class}
                              size="small"
                              variant="outlined"
                              onClick={() =>
                                setFilter({
                                  source: "provider_listings",
                                  website: row.source_url,
                                  category: category.error_class,
                                })
                              }
                            >
                              {label(category.error_class)} · {category.count}
                            </Button>
                          ))}
                        </Stack>
                      </TableCell>
                      <TableCell>
                        <Button
                          size="small"
                          onClick={() =>
                            setFilter({
                              source: "provider_listings",
                              website: row.source_url,
                              category: "",
                            })
                          }
                        >
                          {row.count}
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
            <Pager
              total={websites.length}
              page={page}
              size={size}
              onPage={setPage}
              onSize={(n) => {
                setSize(n);
                setPage(0);
              }}
            />
          </>
        )}
      </Paper>
      {filter && (
        <FailureDetails
          filter={filter}
          range={range}
          onClose={() => setFilter(null)}
        />
      )}
    </>
  );
}
