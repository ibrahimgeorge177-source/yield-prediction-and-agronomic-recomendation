/** Inline icons: 20px stroke set, so nothing extra is fetched at run time. */

const base = {
  width: 18,
  height: 18,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.7,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
}

export const IconHome = (p) => (
  <svg {...base} {...p}>
    <path d="M3 10.5 12 4l9 6.5" />
    <path d="M5.5 9.8V19a1 1 0 0 0 1 1h11a1 1 0 0 0 1-1V9.8" />
  </svg>
)

export const IconForecast = (p) => (
  <svg {...base} {...p}>
    <path d="M3 19h18" />
    <path d="M6 19v-6" />
    <path d="M11 19V8" />
    <path d="M16 19v-9" />
    <path d="M21 19V5" />
  </svg>
)

export const IconAdvisor = (p) => (
  <svg {...base} {...p}>
    <path d="M12 3v3" />
    <path d="M12 21v-3" />
    <path d="M12 18a6 6 0 1 0 0-12 6 6 0 0 0 0 12Z" />
    <path d="m9.5 12 1.8 1.8L15 10" />
  </svg>
)

export const IconModel = (p) => (
  <svg {...base} {...p}>
    <circle cx="6" cy="7" r="2.2" />
    <circle cx="18" cy="7" r="2.2" />
    <circle cx="12" cy="17" r="2.2" />
    <path d="M7.7 8.6 10.6 15" />
    <path d="M16.3 8.6 13.4 15" />
    <path d="M8.2 7h7.6" />
  </svg>
)

export const IconDocs = (p) => (
  <svg {...base} {...p}>
    <path d="M6 3h8l4 4v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" />
    <path d="M14 3v5h4" />
    <path d="M8.5 13h7" />
    <path d="M8.5 17h5" />
  </svg>
)

export const IconMaize = (p) => (
  <svg {...base} strokeWidth={1.5} {...p}>
    <path d="M12 21c0-5 0-9 0-12" />
    <path d="M12 9c0-3.3 2.2-6 5-6 0 3.3-2.2 6-5 6Z" />
    <path d="M12 13c0-3.3-2.2-6-5-6 0 3.3 2.2 6 5 6Z" />
    <path d="M12 17c0-2.8 1.9-5 4.3-5 0 2.8-1.9 5-4.3 5Z" />
  </svg>
)

export const IconRefresh = (p) => (
  <svg {...base} {...p}>
    <path d="M20 11A8 8 0 0 0 6.3 6.3L4 8.5" />
    <path d="M4 4v4.5h4.5" />
    <path d="M4 13a8 8 0 0 0 13.7 4.7L20 15.5" />
    <path d="M20 20v-4.5h-4.5" />
  </svg>
)

export const IconSun = (p) => (
  <svg {...base} {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </svg>
)

export const IconMoon = (p) => (
  <svg {...base} {...p}>
    <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z" />
  </svg>
)

export const IconPlus = (p) => (
  <svg {...base} {...p}>
    <path d="M12 5v14M5 12h14" />
  </svg>
)

export const IconTrash = (p) => (
  <svg {...base} {...p}>
    <path d="M4 7h16" />
    <path d="M9 7V5h6v2" />
    <path d="M6.5 7 7 20h10l.5-13" />
  </svg>
)

export const IconArrow = (p) => (
  <svg {...base} {...p}>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </svg>
)

export const IconGitHub = (p) => (
  <svg {...base} strokeWidth={0} fill="currentColor" {...p}>
    <path d="M12 2C6.5 2 2 6.6 2 12.3c0 4.5 2.9 8.4 6.8 9.7.5.1.7-.2.7-.5v-1.9c-2.8.6-3.4-1.2-3.4-1.2-.5-1.2-1.1-1.5-1.1-1.5-.9-.6.1-.6.1-.6 1 .1 1.5 1 1.5 1 .9 1.6 2.4 1.1 3 .9.1-.7.4-1.1.6-1.4-2.2-.3-4.6-1.2-4.6-5.2 0-1.1.4-2.1 1-2.8-.1-.3-.4-1.4.1-2.8 0 0 .8-.3 2.8 1.1a9.3 9.3 0 0 1 5 0c1.9-1.4 2.7-1.1 2.7-1.1.6 1.4.2 2.5.1 2.8.7.7 1 1.7 1 2.8 0 4-2.3 4.9-4.6 5.2.4.3.7.9.7 1.9v2.8c0 .3.2.6.7.5A10.3 10.3 0 0 0 22 12.3C22 6.6 17.5 2 12 2Z" />
  </svg>
)
