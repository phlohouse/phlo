/**
 * Storybook preview entry loading the shared Mission Control stylesheet.
 */
import "./preview.css";
import type { Preview } from "@storybook/react";

const preview: Preview = {
  parameters: { layout: "padded" },
};
export default preview;
