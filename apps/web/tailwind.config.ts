import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
    "../../packages/ui/src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        navy: {
          DEFAULT: "var(--color-navy, #102C54)",
          dark: "#0b203d",
          light: "#173B6B",
        },
        primary: {
          DEFAULT: "var(--color-primary, #2A5BDB)",
          hover: "var(--color-primary-hover, #214FC5)",
          light: "#3675EA",
        },
        "royal-blue": {
          DEFAULT: "var(--color-primary, #2A5BDB)",
        },
        surface: {
          DEFAULT: "var(--color-surface, #FFFFFF)",
          hover: "var(--color-surface-hover, #F1F4FA)",
        },
        appBg: "var(--color-background, #F8F9FD)",
        textPrimary: "var(--color-text-primary, #19365F)",
        textSecondary: "var(--color-text-secondary, #657895)",
        textMuted: "var(--color-text-muted, #8796AD)",
        appBorder: "var(--color-border, #DFE5EF)",
      },
      boxShadow: {
        card: "var(--shadow-card, 0 8px 28px rgba(16, 44, 84, 0.10))",
        button: "var(--shadow-button, 0 4px 12px rgba(42, 91, 219, 0.25))",
      },
      borderRadius: {
        small: "var(--radius-small, 6px)",
        medium: "var(--radius-medium, 10px)",
        large: "var(--radius-large, 16px)",
      },
    },
  },
  plugins: [],
};

export default config;
