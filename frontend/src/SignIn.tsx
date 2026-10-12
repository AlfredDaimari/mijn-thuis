import { useState, type FormEvent } from "react";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  Paper,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { api } from "./api";

export default function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password, remember }),
      });
      onSignedIn();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Box
      sx={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        p: 3,
        background: "linear-gradient(140deg,#e8f3ef,#edf2fa)",
      }}
    >
      <Paper
        elevation={0}
        sx={{
          width: "100%",
          maxWidth: 420,
          p: { xs: 3, sm: 5 },
          border: "1px solid #dce7e2",
          borderRadius: 4,
        }}
      >
        <Stack component="form" onSubmit={submit} gap={2.5}>
          <Typography variant="h3">🏡</Typography>
          <Box>
            <Typography variant="h4" fontWeight={800}>
              Welcome home.
            </Typography>
            <Typography color="text.secondary" mt={1}>
              Sign in to your housing dashboard.
            </Typography>
          </Box>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField
            label="Username"
            slotProps={{ inputLabel: { shrink: true } }}
            autoComplete="username"
            required
            autoFocus
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
          <TextField
            label="Password"
            slotProps={{ inputLabel: { shrink: true } }}
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <FormControlLabel
            control={
              <Checkbox
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
              />
            }
            label="Keep me signed in for 14 days"
          />
          <Button
            variant="contained"
            size="large"
            type="submit"
            disabled={busy}
          >
            {busy ? "Signing in…" : "Sign in →"}
          </Button>
          <Typography variant="caption" color="text.secondary">
            An essential session cookie keeps you signed in. No tracking
            cookies.
          </Typography>
        </Stack>
      </Paper>
    </Box>
  );
}
