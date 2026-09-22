import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom";
import ErrorState from "@/components/ErrorState";

// Mock next/link
jest.mock("next/link", () => {
  return function MockLink({ children, href }: { children: React.ReactNode; href: string }) {
    return <a href={href}>{children}</a>;
  };
});

describe("ErrorState", () => {
  it("renders network error with retry button", () => {
    const onRetry = jest.fn();
    render(<ErrorState type="network" onRetry={onRetry} />);
    expect(screen.getByText("Can't connect to server")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Try again"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("renders not-found with dashboard link", () => {
    render(<ErrorState type="not-found" />);
    expect(screen.getByText("Sample not found")).toBeInTheDocument();
    expect(screen.getByText("Go to Dashboard")).toBeInTheDocument();
  });

  it("renders empty with custom message", () => {
    render(<ErrorState type="empty" message="No results found." />);
    expect(screen.getByText("No matching samples")).toBeInTheDocument();
    expect(screen.getByText("No results found.")).toBeInTheDocument();
  });

  it("renders generic error", () => {
    render(<ErrorState type="generic" />);
    expect(screen.getByText("Something went wrong")).toBeInTheDocument();
  });
});
