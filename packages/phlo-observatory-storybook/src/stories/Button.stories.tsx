/**
 * Button stories: primary, secondary, destructive variants.
 */
import type { Meta, StoryObj } from "@storybook/react";
import { Button } from "../../../phlo-observatory/web/src/components/ui/button";

const meta: Meta<typeof Button> = { title: "UI/Button", component: Button };
export default meta;

export const Primary: StoryObj<typeof Button> = { render: () => <Button>Save view</Button> };
export const Secondary: StoryObj<typeof Button> = { render: () => <Button variant="secondary">Columns</Button> };
export const Destructive: StoryObj<typeof Button> = { render: () => <Button variant="destructive">Delete</Button> };
