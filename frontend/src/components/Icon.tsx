import type { CSSProperties, ComponentType } from "react";
import {
  IconAlertTriangle,
  IconArrowLeft,
  IconBook2,
  IconCheck,
  IconChevronDown,
  IconDots,
  IconExternalLink,
  IconFile,
  IconFolder,
  IconHome,
  IconInfoCircle,
  IconLock,
  IconLogout,
  IconPencil,
  IconPlus,
  IconQuote,
  IconSend,
  IconShieldCheck,
  IconTrash,
  IconUpload,
  IconUser,
  IconX,
} from "@tabler/icons-react";

type IconName =
  | "book"
  | "lock"
  | "file"
  | "upload"
  | "send"
  | "quote"
  | "logout"
  | "chevron"
  | "trash"
  | "plus"
  | "shield"
  | "close"
  | "folder"
  | "home"
  | "more"
  | "external"
  | "user"
  | "back"
  | "check"
  | "edit"
  | "alert"
  | "info";

type TablerIconProps = {
  size?: number;
  stroke?: number;
  style?: CSSProperties;
  "aria-hidden"?: boolean;
};

const icons: Record<IconName, ComponentType<TablerIconProps>> = {
  alert: IconAlertTriangle,
  info: IconInfoCircle,
  check: IconCheck,
  edit: IconPencil,
  close: IconX,
  folder: IconFolder,
  home: IconHome,
  more: IconDots,
  external: IconExternalLink,
  user: IconUser,
  back: IconArrowLeft,
  book: IconBook2,
  lock: IconLock,
  file: IconFile,
  upload: IconUpload,
  send: IconSend,
  quote: IconQuote,
  logout: IconLogout,
  chevron: IconChevronDown,
  trash: IconTrash,
  plus: IconPlus,
  shield: IconShieldCheck,
};

export function Icon({
  name,
  size = 20,
  style,
}: {
  name: IconName;
  size?: number;
  style?: CSSProperties;
}) {
  const Component = icons[name];
  return <Component size={size} stroke={1.7} style={style} aria-hidden />;
}

export function Brand() {
  return (
    <span className="brand">
      <span className="brand__symbol">
        <Icon name="book" size={22} />
      </span>
      <span>知识工作台</span>
    </span>
  );
}
