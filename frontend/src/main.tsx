import { CssBaseline, ThemeProvider, createTheme } from "@mui/material";
import { createRoot } from "react-dom/client";
import App from "./App";

const theme = createTheme({
  palette: {
    mode: "light",
    primary: { main: "#153b31" },
    secondary: { main: "#e69a47" },
    background: { default: "#f5f6f2", paper: "#ffffff" },
  },
  shape: { borderRadius: 10 },
  typography: { fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif" },
});

createRoot(document.getElementById("root")!).render(
  <ThemeProvider theme={theme}>
    <CssBaseline />
    <App />
  </ThemeProvider>,
);
