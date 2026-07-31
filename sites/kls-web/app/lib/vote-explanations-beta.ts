export type VoteExplanationExample = {
  lawmaker: string;
  party: string;
  district: string;
  vote: "Yes" | "No";
  reasonStatus: "found" | "not_found";
  reason: string;
  sourceLabel: string;
  sourceUrl: string;
  sourceAction: string;
};

const houseVideo = "https://www.youtube.com/watch?v=X45rOkJsR2g";

export const wyomingVoteExplanationBeta = {
  latestVideoChecked: "March 11, 2026",
  bill: {
    number: "SF 101",
    title: "Second Amendment Protection Act amendments",
    chamber: "Wyoming House",
    voteDate: "March 5, 2026",
    result: "Passed 40 to 21",
    officialUrl: "https://www.wyoleg.gov/Legislation/2026/SF0101",
  },
  examples: [
    {
      lawmaker: "Art Washut",
      party: "Republican",
      district: "House District 36",
      vote: "No",
      reasonStatus: "found",
      reason:
        "He said the bill was too vague for officers making fast decisions. He also opposed exposing agencies to a $50,000 civil penalty when people disagreed about what the bill meant.",
      sourceLabel: "House floor statement at 2:21:04",
      sourceUrl: `${houseVideo}&t=8464s`,
      sourceAction: "Watch his statement",
    },
    {
      lawmaker: "Pam Thayer",
      party: "Republican",
      district: "House District 15",
      vote: "No",
      reasonStatus: "found",
      reason:
        "She said she supports Second Amendment rights, but the bill was too vague and left too much open to interpretation. She chose to support local law enforcement.",
      sourceLabel: "House floor statement at 2:28:11",
      sourceUrl: `${houseVideo}&t=8891s`,
      sourceAction: "Watch her statement",
    },
    {
      lawmaker: "Elissa Campbell",
      party: "Republican",
      district: "House District 56",
      vote: "No",
      reasonStatus: "found",
      reason:
        "She said she supports the Second Amendment, but local officers warned that the bill could keep them from doing their jobs during domestic violence calls. She voted no to back local law enforcement.",
      sourceLabel: "House floor statement at 2:32:26",
      sourceUrl: `${houseVideo}&t=9146s`,
      sourceAction: "Watch her statement",
    },
    {
      lawmaker: "Scott Smith",
      party: "Republican",
      district: "House District 5",
      vote: "Yes",
      reasonStatus: "not_found",
      reason: "Couldn't find a published reason.",
      sourceLabel: "House floor debate begins at 2:20:51",
      sourceUrl: `${houseVideo}&t=8451s`,
      sourceAction: "Review the debate",
    },
  ] satisfies VoteExplanationExample[],
};
