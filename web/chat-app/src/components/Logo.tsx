// Знак: крыша над лупой — «поиск жилья». Цвета — из темы (--primary и цвет на нём).
export function LogoMark({ size = 28 }: { size?: number }) {
  return (
    <svg className="block flex-none" viewBox="0 0 32 32" width={size} height={size} aria-hidden="true" focusable="false">
      <rect width="32" height="32" rx="7" fill="var(--primary)" />
      <path d="M7 15.5 16 8l9 7.5" fill="none" stroke="var(--primary-foreground)" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="15" cy="19.5" r="4.2" fill="none" stroke="var(--primary-foreground)" strokeWidth="2.2" />
      <path d="m18.2 22.7 3.3 3.3" stroke="var(--primary-foreground)" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  );
}

export function Logo() {
  return (
    <span className="inline-flex items-center gap-2.5">
      <LogoMark />
      <span className="font-serif text-xl font-bold tracking-tight">Prop Mind</span>
    </span>
  );
}
