/**
 * Card stories: basic and wide layouts.
 */
import type { Meta, StoryObj } from "@storybook/react";
import { Card, CardTitle } from "../../../phlo-observatory/web/src/components/ui/card";

const meta: Meta = { title: "UI/Card" };
export default meta;

export const Basic: StoryObj = { render: () => <Card><CardTitle>Needs attention</CardTitle><p style={{ fontSize: 13 }}>3 alerts · 1 expiring reagent</p></Card> };
export const Wide: StoryObj = { render: () => <Card style={{ maxWidth: 480 }}><CardTitle>IC50 fit — plate P-1042</CardTitle><p style={{ fontSize: 13 }}>IC50 38 nM · R² 0.98</p></Card> };
