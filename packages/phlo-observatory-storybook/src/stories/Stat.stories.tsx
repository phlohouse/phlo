/**
 * Stat stories: label, value, hint combinations.
 */
import type { Meta, StoryObj } from "@storybook/react";
import { Stat } from "../../../phlo-observatory/web/src/components/ui/stat";

const meta: Meta = { title: "UI/Stat" };
export default meta;

export const Strip: StoryObj = {
  render: () => (
    <div style={{ display: "flex", gap: 12 }}>
      <Stat label="Active" value="4" /><Stat label="Reviews" value="3" hint="due Fri" />
    </div>
  ),
};
export const Single: StoryObj = { render: () => <Stat label="Mean Ct" value="25.6" hint="n=8" /> };
