/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: { sans: ['Inter', 'system-ui', 'sans-serif'] },
      colors: {
        ink: '#0B0D11',
        panel: '#12151B',
        line: '#1E232C',
        acc: '#E9A178',
        ok: '#5FBF7F',
        warn: '#E5B95C',
        bad: '#E2574C',
      },
    },
  },
  plugins: [],
}
