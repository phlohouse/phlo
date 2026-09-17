/**
 * FilterBar stories: default, minimal, registry variants.
 */
import type { Meta, StoryObj } from "@storybook/react";
import { FilterBar } from "../../../phlo-observatory/web/src/components/FilterBar";

const meta: Meta = { title: "UI/FilterBar" };
export default meta;

export const Default: StoryObj = { render: () => <FilterBar selects={["Status", "Owner"]} /> };
export const Minimal: StoryObj = { render: () => <FilterBar selects={["Kind"]} /> };
export const Registry: StoryObj = { render: () => <FilterBar selects={["Type", "Status", "Location"]} /> };
