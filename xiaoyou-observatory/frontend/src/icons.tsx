import type { SVGProps } from 'react'

type IconProps = SVGProps<SVGSVGElement>

function IconBase({ children, ...props }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      {children}
    </svg>
  )
}

export const HeartPulseIcon = (props: IconProps) => (
  <IconBase {...props}><path d="M3 12h4l2-5 3.4 10 2.2-5H21"/><path d="M19 5.5a5.2 5.2 0 0 0-7 0 5.2 5.2 0 0 0-7 0c-2.7 2.7-2.7 7 0 9.7L12 22l7-6.8c2.7-2.7 2.7-7 0-9.7Z" opacity=".35"/></IconBase>
)

export const LinkIcon = (props: IconProps) => (
  <IconBase {...props}><path d="M10 13a5 5 0 0 0 7.5.5l2-2a5 5 0 0 0-7-7l-1.1 1.1"/><path d="M14 11a5 5 0 0 0-7.5-.5l-2 2a5 5 0 0 0 7 7l1.1-1.1"/></IconBase>
)

export const SparkIcon = (props: IconProps) => (
  <IconBase {...props}><path d="m12 2 1.6 5.4L19 9l-5.4 1.6L12 16l-1.6-5.4L5 9l5.4-1.6L12 2Z"/><path d="m19 15 .8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15Z"/></IconBase>
)

export const MemoryIcon = (props: IconProps) => (
  <IconBase {...props}><path d="M9.5 4.5A3.5 3.5 0 0 0 6 8v1a3 3 0 0 0-1 5.8V16a4 4 0 0 0 4 4h1V4.5h-.5Z"/><path d="M14.5 4.5A3.5 3.5 0 0 1 18 8v1a3 3 0 0 1 1 5.8V16a4 4 0 0 1-4 4h-1V4.5h.5Z"/><path d="M7 10h3M14 10h3M7 15h3M14 15h3"/></IconBase>
)

export const VisionIcon = (props: IconProps) => (
  <IconBase {...props}><path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z"/><circle cx="12" cy="12" r="3"/><path d="m16.5 4.5 1-2m2 4 2-1"/></IconBase>
)

export const PowerIcon = (props: IconProps) => (
  <IconBase {...props}><path d="M12 2v10"/><path d="M18.4 6.6a9 9 0 1 1-12.8 0"/></IconBase>
)

export const RestartIcon = (props: IconProps) => (
  <IconBase {...props}><path d="M20 7v5h-5"/><path d="M4 17v-5h5"/><path d="M6.1 8A7 7 0 0 1 18.8 6L20 12M4 12l1.2 6A7 7 0 0 0 18 16"/></IconBase>
)

export const StopIcon = (props: IconProps) => (
  <IconBase {...props}><rect x="5" y="5" width="14" height="14" rx="2"/></IconBase>
)

export const LogIcon = (props: IconProps) => (
  <IconBase {...props}><path d="M6 2h9l4 4v16H6z"/><path d="M14 2v5h5M9 12h6M9 16h6"/></IconBase>
)

export const QrIcon = (props: IconProps) => (
  <IconBase {...props}><rect x="3" y="3" width="6" height="6"/><rect x="15" y="3" width="6" height="6"/><rect x="3" y="15" width="6" height="6"/><path d="M15 15h2v2h-2zM19 15h2v6h-2M15 19h2v2h-2"/></IconBase>
)

export const ShieldIcon = (props: IconProps) => (
  <IconBase {...props}><path d="M12 2 4 5v6c0 5 3.4 8.7 8 11 4.6-2.3 8-6 8-11V5l-8-3Z"/><path d="m9 12 2 2 4-5"/></IconBase>
)

export const CloseIcon = (props: IconProps) => (
  <IconBase {...props}><path d="m6 6 12 12M18 6 6 18"/></IconBase>
)

export const RefreshIcon = (props: IconProps) => (
  <IconBase {...props}><path d="M20 11a8 8 0 1 0-2.3 5.7L20 14"/><path d="M20 6v5h-5"/></IconBase>
)

export const LogoutIcon = (props: IconProps) => (
  <IconBase {...props}><path d="M10 4H4v16h6M14 8l4 4-4 4M8 12h10"/></IconBase>
)

export const ClockIcon = (props: IconProps) => (
  <IconBase {...props}><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></IconBase>
)
