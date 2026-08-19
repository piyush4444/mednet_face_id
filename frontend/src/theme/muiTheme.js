import { createTheme } from "@mui/material/styles";

// Keep MUI's palettes aligned with the Tailwind CSS variables in index.css
// (light values from @theme, dark values from the `.dark` override block).
// Header, buttons, date picker highlights, drawers, and any future MUI
// component all pick up the active mode automatically.
const palettes = {
  light: {
    mode: "light",
    primary: {
      main: "#7C3AED",
      light: "#A78BFA",
      dark: "#6D28D9",
      contrastText: "#FFFFFF",
    },
    secondary: {
      main: "#EDE9FE",
      contrastText: "#4C1D95",
    },
    background: {
      default: "#FFFFFF",
      paper: "#FFFFFF",
    },
    text: {
      primary: "#0F172A",
      secondary: "#64748B",
    },
  },
  dark: {
    mode: "dark",
    primary: {
      main: "#8B5CF6",
      light: "#A78BFA",
      dark: "#7C3AED",
      contrastText: "#FFFFFF",
    },
    secondary: {
      main: "#2E2749",
      contrastText: "#DDD6FE",
    },
    background: {
      default: "#0B0D14",
      paper: "#131722",
    },
    text: {
      primary: "#F1F5F9",
      secondary: "#94A3B8",
    },
  },
};

export function getMuiTheme(mode = "light") {
  return createTheme({
    palette: palettes[mode] ?? palettes.light,
    shape: {
      borderRadius: 12,
    },
    typography: {
      fontFamily:
        'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    },
  });
}

export default getMuiTheme("light");
