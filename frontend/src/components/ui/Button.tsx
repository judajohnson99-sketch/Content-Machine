import type { ButtonHTMLAttributes } from "react";
import styles from "./Button.module.css";

type Variant = "primary" | "secondary" | "success" | "danger" | "ghost";
type Size = "sm" | "md";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

// The one button every trigger/decision control renders through. Always a
// native <button> so disabled state, title tooltips and accessible names
// keep working exactly as callers (and their tests) expect - this only
// adds consistent styling on top.
export function Button({ variant = "secondary", size = "md", className, type = "button", ...rest }: Props) {
  const classes = [styles.button, styles[variant], styles[size], className].filter(Boolean).join(" ");
  return <button type={type} className={classes} {...rest} />;
}
