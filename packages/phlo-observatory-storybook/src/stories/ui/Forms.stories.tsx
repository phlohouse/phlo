/**
 * Form primitives: input, label, select, checkbox and switch.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

const meta: Meta = { title: "UI/Forms" };
export default meta;

export const TextInputs: StoryObj = {
  render: () => (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, width: 320 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <Label htmlFor="name">Column name</Label>
        <Input id="name" placeholder="e.g. signal_rlu" />
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <Label htmlFor="disabled">Disabled</Label>
        <Input id="disabled" defaultValue="Read only" disabled />
      </div>
    </div>
  ),
};

export const Selects: StoryObj = {
  render: () => (
    <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
      <Select defaultValue="__all__">
        <SelectTrigger className="h-[34px] w-auto gap-1.5 rounded-[7px] text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__all__">Status: all</SelectItem>
          <SelectItem value="available">available</SelectItem>
          <SelectItem value="in use">in use</SelectItem>
          <SelectItem value="expired">expired</SelectItem>
        </SelectContent>
      </Select>
      <Select defaultValue="4c">
        <SelectTrigger className="h-[34px] w-auto gap-1.5 rounded-[7px] text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">Condition: all</SelectItem>
          <SelectItem value="4c">4 °C</SelectItem>
          <SelectItem value="-20c">−20 °C</SelectItem>
        </SelectContent>
      </Select>
    </div>
  ),
};

export const ChecksAndToggles: StoryObj = {
  render: () => (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Checkbox id="frozen" defaultChecked />
        <Label htmlFor="frozen">Freeze first column</Label>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Checkbox id="unchecked" />
        <Label htmlFor="unchecked">Randomize well order</Label>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Switch id="notify" defaultChecked />
        <Label htmlFor="notify">Notify on release block</Label>
      </div>
    </div>
  ),
};
