/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./templates/**/*.jinja2",
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          DEFAULT: '#1e40af',
          dark: '#1e3a8a',
        },
        saffron: '#ff6b00',
        'indian-green': '#138808',
        accent: {
          DEFAULT: '#dc2626',
          light: '#ef4444',
        },
        bg: '#f7fafc',
        'card-bg': '#ffffff',
        text: {
          DEFAULT: '#2d3748',
          light: '#718096',
        },
        border: '#e2e8f0',
        success: '#138808',
        warning: '#ff6b00',
        danger: '#dc2626',
      },
    },
  },
  plugins: [],
}
