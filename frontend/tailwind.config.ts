/**
 * Tailwind CSS 主题配置（Apple HIG + Block Studio 风格）
 * - 系统字体栈
 * - Apple 官网主蓝 #0071e3
 * - Squircle 圆角（3xl=24px 连续曲率）
 * - 5 级柔和阴影 + glass border
 * - 弹簧曲线 cubic-bezier(0.25, 1, 0.5, 1)
 * - fade-in-up 入场动画
 * - dark mode: class 策略
 */
import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          '"SF Pro Display"',
          '"SF Pro Text"',
          '"Helvetica Neue"',
          '"PingFang SC"',
          '"Microsoft YaHei"',
          "sans-serif",
        ],
        mono: [
          '"SF Mono"',
          "Menlo",
          "Monaco",
          '"JetBrains Mono"',
          '"SFMono-Regular"',
          "Consolas",
          "monospace",
        ],
      },
      colors: {
        // Apple 官网主蓝（更准确的 #0071e3）
        "apple-blue": {
          DEFAULT: "#0071e3",
          hover: "#0077ed",
          active: "#006edb",
        },
        // 系统灰 6 级
        gray: {
          50: "#FBFBFD", // 画布底色（Apple 官网）
          100: "#F5F5F7",
          200: "#E5E5EA", // 次级按钮色
          300: "#D1D1D6",
          400: "#AEAEB2",
          500: "#86868B", // 注释色（Apple 官网）
          600: "#636366",
          700: "#48484A",
          800: "#3A3A3C",
          900: "#1C1C1E",
          950: "#000000",
        },
        // Apple 语义色
        "apple-green": "#34C759",
        "apple-red": "#FF3B30",
        "apple-orange": "#FF9500",
        "apple-yellow": "#FFCC00",
        "apple-purple": "#AF52DE",
        "apple-pink": "#FF2D55",
      },
      boxShadow: {
        // Apple 风阴影：极淡、多层、向下
        xs: "0 1px 2px rgba(0, 0, 0, 0.04)",
        sm: "0 2px 8px rgba(0, 0, 0, 0.06), 0 1px 2px rgba(0, 0, 0, 0.04)",
        DEFAULT:
          "0 4px 16px rgba(0, 0, 0, 0.08), 0 2px 4px rgba(0, 0, 0, 0.04)",
        md: "0 8px 24px rgba(0, 0, 0, 0.1), 0 4px 8px rgba(0, 0, 0, 0.05)",
        lg: "0 16px 40px rgba(0, 0, 0, 0.12), 0 6px 12px rgba(0, 0, 0, 0.06)",
        xl: "0 32px 64px rgba(0, 0, 0, 0.16), 0 12px 24px rgba(0, 0, 0, 0.08)",
        // 苹果招牌聚焦光晕
        "focus-blue":
          "0 0 0 4px rgba(0, 113, 227, 0.3), 0 0 0 1px rgba(0, 113, 227, 0.5)",
      },
      borderRadius: {
        // Squircle 圆角（连续曲率大圆角）
        xl: "12px",
        "2xl": "16px",
        "3xl": "24px", // Apple 标准卡片圆角
        "4xl": "32px",
      },
      transitionTimingFunction: {
        // 苹果弹簧曲线（control point 模拟 spring）
        spring: "cubic-bezier(0.25, 1, 0.5, 1)",
        // 苹果入场曲线（更轻的 spring）
        "spring-in": "cubic-bezier(0.32, 0.72, 0, 1)",
        // 苹果退出曲线
        "spring-out": "cubic-bezier(0.32, 0, 0.67, 0.19)",
      },
      keyframes: {
        "toast-progress": {
          from: { transform: "scaleX(1)" },
          to: { transform: "scaleX(0)" },
        },
        // 入场上浮（fade-in-up）
        "fade-in-up": {
          "0%": { opacity: "0", transform: "translateY(16px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "fade-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        // 极光浮动（呼吸感）
        "aurora-1": {
          "0%, 100%": { transform: "translate(0, 0) scale(1)" },
          "50%": { transform: "translate(5%, -3%) scale(1.1)" },
        },
        "aurora-2": {
          "0%, 100%": { transform: "translate(0, 0) scale(1)" },
          "50%": { transform: "translate(-4%, 4%) scale(0.95)" },
        },
        "aurora-3": {
          "0%, 100%": { transform: "translate(0, 0) scale(1)" },
          "50%": { transform: "translate(3%, 5%) scale(1.05)" },
        },
      },
      animation: {
        "toast-progress": "toast-progress linear forwards",
        "fade-in-up": "fade-in-up 600ms cubic-bezier(0.32, 0.72, 0, 1) both",
        "fade-in": "fade-in 240ms ease-out both",
        "aurora-1": "aurora-1 18s ease-in-out infinite",
        "aurora-2": "aurora-2 22s ease-in-out infinite",
        "aurora-3": "aurora-3 26s ease-in-out infinite",
      },
    },
  },
  plugins: [],
} satisfies Config;