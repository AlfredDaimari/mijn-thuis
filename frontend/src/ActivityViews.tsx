import { useEffect, useState, Fragment } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  Collapse,
  Dialog,
  DialogContent,
  DialogTitle,
  Link,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TablePagination,
  TableRow,
  Typography,
} from "@mui/material";
import {
  api,
  dateTime,
  label,
  rent,
  type Page,
  type Row,
  type SourceLink,
  type Screenshot,
} from "./api";

export function Status({ value }: { value: string }) {
  return (
    <Chip
      size="small"
      label={label(value)}
      color={
        value === "submitted" || value === "processed"
          ? "success"
          : value === "error"
            ? "error"
            : "default"
      }
    />
  );
}
export function Pager({
  total,
  page,
  size,
  onPage,
  onSize,
}: {
  total: number;
  page: number;
  size: number;
  onPage: (n: number) => void;
  onSize: (n: number) => void;
}) {
  return (
    <TablePagination
      component="div"
      count={total}
      page={page}
      rowsPerPage={size}
      rowsPerPageOptions={[25, 50, 100]}
      onPageChange={(_, next) => onPage(next)}
      onRowsPerPageChange={(e) => onSize(Number(e.target.value))}
    />
  );
}
export function FullLink({ href, title }: { href: string; title?: string }) {
  return (
    <Link
      href={href}
      target="_blank"
      rel="noreferrer"
      sx={{ overflowWrap: "anywhere" }}
    >
      {title || href}
    </Link>
  );
}

function InboxLinks({ id }: { id: number }) {
  const [data, setData] = useState<Page<SourceLink>>({
    items: [],
    total: 0,
    page: 0,
    page_size: 25,
  });
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(25);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api<Page<SourceLink>>(`/inbox/${id}/links?page=${page}&page_size=${size}`)
      .then((result) => {
        if (active) setData(result);
      })
      .catch((reason) => {
        if (active) setError(reason.message);
      });
    return () => {
      active = false;
    };
  }, [id, page, size]);
  return (
    <Box sx={{ p: 2, bgcolor: "#f4f8f7" }}>
      {error && <Alert severity="error">{error}</Alert>}
      <Typography variant="subtitle2" mb={1}>
        🔗 Extracted listing links
      </Typography>
      {data.items.length === 0 && (
        <Typography color="text.secondary">
          No links extracted from this email.
        </Typography>
      )}
      {data.items.map((link) => (
        <Paper key={link.id} variant="outlined" sx={{ p: 1.5, mb: 1 }}>
          <Stack direction="row" justifyContent="space-between" gap={2}>
            <FullLink
              href={link.provider_url || link.stekkies_url}
              title={link.title}
            />
            <Status value={link.status} />
          </Stack>
          <Typography variant="caption" display="block" mt={0.5}>
            <FullLink href={link.stekkies_url} />
          </Typography>
          {link.processing_error && (
            <Typography color="error" variant="body2" mt={1}>
              {link.processing_error}
            </Typography>
          )}
        </Paper>
      ))}
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
    </Box>
  );
}

export function ActivityTable({
  kind,
  rows,
  onEvidence,
}: {
  kind: string;
  rows: Row[];
  onEvidence: (row: Row) => void;
}) {
  const [expanded, setExpanded] = useState<number | null>(null);
  if (!rows.length)
    return (
      <Box textAlign="center" py={8}>
        <Typography fontSize={30}>🌱</Typography>
        <Typography color="text.secondary">
          No records match these filters.
        </Typography>
      </Box>
    );
  if (kind === "inbox")
    return (
      <TableContainer sx={{ maxHeight: 620 }}>
        <Table size="small" stickyHeader>
          <TableHead>
            <TableRow>
              <TableCell>Email</TableCell>
              <TableCell>Loaded</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Listing links</TableCell>
              <TableCell>Failures</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((row) => (
              <Fragment key={row.id}>
                <TableRow hover>
                  <TableCell>
                    <Link
                      component="button"
                      textAlign="left"
                      onClick={() =>
                        setExpanded(expanded === row.id ? null : row.id)
                      }
                    >
                      {expanded === row.id ? "▾" : "▸"}{" "}
                      {row.subject || "(no subject)"}
                    </Link>
                    <Typography
                      variant="caption"
                      display="block"
                      color="text.secondary"
                    >
                      {row.sender}
                    </Typography>
                  </TableCell>
                  <TableCell>{dateTime(row.accessed_at)}</TableCell>
                  <TableCell>
                    <Status value={row.status} />
                  </TableCell>
                  <TableCell>{row.link_count}</TableCell>
                  <TableCell>
                    {row.processing_error
                      ? row.processing_error
                      : row.link_errors
                        ? `${row.link_errors} link errors`
                        : "—"}
                  </TableCell>
                </TableRow>
                <TableRow>
                  <TableCell colSpan={5} sx={{ py: 0, border: 0 }}>
                    <Collapse in={expanded === row.id} unmountOnExit>
                      {expanded === row.id && <InboxLinks id={row.id} />}
                    </Collapse>
                  </TableCell>
                </TableRow>
              </Fragment>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    );
  return (
    <TableContainer sx={{ maxHeight: 620 }}>
      <Table size="small" stickyHeader>
        <TableHead>
          <TableRow>
            <TableCell>Home</TableCell>
            <TableCell>Location / rent</TableCell>
            <TableCell>Status</TableCell>
            <TableCell>
              {kind === "applications" ? "Submitted" : "Last attempt"}
            </TableCell>
            <TableCell>Evidence / failure</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.id} hover>
              <TableCell sx={{ maxWidth: 420 }}>
                <Typography fontWeight={600} mb={0.5}>
                  {row.provider_title || `Listing #${row.id}`}
                </Typography>
                {row.provider_url && <FullLink href={row.provider_url} />}
                <Typography
                  variant="caption"
                  display="block"
                  mt={0.5}
                  color="text.secondary"
                >
                  {row.room_count || "—"} rooms · {row.area_m2 || "—"} m²
                </Typography>
              </TableCell>
              <TableCell>
                {row.location || "—"}
                <Typography variant="body2" fontWeight={600}>
                  {rent(row.monthly_rent_cents)}
                </Typography>
              </TableCell>
              <TableCell>
                <Status value={row.status} />
              </TableCell>
              <TableCell>
                {dateTime(row.processed_at || row.accessed_at)}
              </TableCell>
              <TableCell sx={{ maxWidth: 330, overflowWrap: "anywhere" }}>
                {!!row.evidence_count && (
                  <Button size="small" onClick={() => onEvidence(row)}>
                    🖼️ {row.evidence_count} screenshots
                  </Button>
                )}
                {row.processing_error && (
                  <Typography variant="body2" color="error">
                    {row.processing_error}
                  </Typography>
                )}
                {!row.evidence_count && !row.processing_error && "—"}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

export function EvidenceDialog({
  row,
  onClose,
}: {
  row: Row;
  onClose: () => void;
}) {
  const [data, setData] = useState<Page<Screenshot>>({
    items: [],
    total: 0,
    page: 0,
    page_size: 25,
  });
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(25);
  const [preview, setPreview] = useState<Screenshot | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api<Page<Screenshot>>(`/evidence/${row.id}?page=${page}&page_size=${size}`)
      .then((result) => {
        if (active) setData(result);
      })
      .catch((reason) => {
        if (active) setError(reason.message);
      });
    return () => {
      active = false;
    };
  }, [row.id, page, size]);
  return (
    <Dialog open onClose={onClose} maxWidth="lg" fullWidth>
      <DialogTitle>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
        >
          <span>🖼️ {row.provider_title || `Listing #${row.id}`}</span>
          <Button onClick={onClose}>Close</Button>
        </Stack>
      </DialogTitle>
      <DialogContent>
        {error && <Alert severity="error">{error}</Alert>}
        <Status value={row.status} />
        {row.provider_url && (
          <Typography variant="body2" my={1}>
            <FullLink href={row.provider_url} />
          </Typography>
        )}
        {preview ? (
          <>
            <Button onClick={() => setPreview(null)}>← All screenshots</Button>
            <Box
              component="img"
              src={preview.nginx_path}
              alt={preview.stage}
              sx={{ maxWidth: "100%", display: "block" }}
            />
          </>
        ) : (
          <Stack direction="row" gap={2} flexWrap="wrap" mt={2}>
            {data.items.map((item) => (
              <Paper
                key={item.screenshot_id}
                variant="outlined"
                sx={{ width: 260, overflow: "hidden" }}
              >
                <Box
                  component="button"
                  onClick={() => setPreview(item)}
                  sx={{
                    width: "100%",
                    p: 0,
                    border: 0,
                    bgcolor: "transparent",
                    cursor: "pointer",
                  }}
                >
                  <Box
                    component="img"
                    src={item.nginx_path}
                    alt={label(item.stage)}
                    sx={{ width: "100%", height: 170, objectFit: "cover" }}
                    onError={(e) => {
                      e.currentTarget.alt = "Screenshot file unavailable";
                    }}
                  />
                </Box>
                <Box p={1.5}>
                  <Typography fontWeight={600}>{label(item.stage)}</Typography>
                  <Typography variant="caption">
                    Attempt {item.attempt_count} · {dateTime(item.captured_at)}
                  </Typography>
                </Box>
              </Paper>
            ))}
          </Stack>
        )}
        {!data.items.length && !error && (
          <Typography my={3}>
            No screenshots were recorded for this attempt.
          </Typography>
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
      </DialogContent>
    </Dialog>
  );
}
