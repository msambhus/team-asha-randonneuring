-- 077_brevethub_waiver_seed.sql
-- Adds initials + date columns to waiver acceptance and seeds SFR waiver text.

-- New columns on rp_waiver_acceptance
ALTER TABLE rp_waiver_acceptance ADD COLUMN IF NOT EXISTS initials TEXT;
ALTER TABLE rp_waiver_acceptance ADD COLUMN IF NOT EXISTS waiver_signed_date DATE;

-- Replace the global default waiver with the exact SFR SmartWaiver text.
-- UPDATE covers the placeholder row seeded in migration 075;
-- INSERT covers a fresh database with no global default yet.
UPDATE rp_waiver_version
SET
  version_label = 'SFR-2025-v2',
  waiver_text   = $WAIVER$I, IN CONSIDERATION of being permitted to participate in any way in the San Francisco Randonneurs' Designated Event, ("Activity"), I hereby acknowledge, agree, attest and represent the following:

1. I FULLY UNDERSTAND that: (a) bicycle riding is dangerous and represents an extreme test of a person's physical and mental limits. I understand that participation involves risks and dangers which include, without limitation, the potential for serious bodily injury, permanent disability, paralysis, illness and death, including exposure to viral infections such as COVID-19; loss of, or damage, to equipment/property; exposure to extreme conditions and circumstances; contact or collision with other bicycle riders, people, vehicles, animals, or other natural or manmade objects; imperfect course conditions; road and surface hazards; inadequate safety measures; other riders of varying skill levels; situations beyond the immediate control of anyone; and other undefined risks and dangers which may not be readily foreseeable or are presently unknown ("Risks"); (b) I understand that these Risks may be caused in whole or in part by my own actions or inactions, the actions or inactions of others, or the acts, inaction or negligence of the Released Parties defined below, and (c) there may be other risks and social and economic losses, costs and damages to me, my family members and dependents either not known to me or not readily foreseeable at this time; and I FULLY ACCEPT AND ASSUME ALL SUCH RISKS AND ALL RESPONSIBILITY FOR ALL LOSSES, COSTS, AND DAMAGES I, my family members and dependents may incur as a result of my participating and riding in the Activity.

2. I am qualified, in good health, and in proper physical condition to participate in the Activity. I agree and warrant that if, at any time, I believe conditions, including road hazards, to be unsafe or if I am not feeling well, I will immediately discontinue further riding of the Activity.

3. TO THE FULLEST EXTENT PERMITTED BY LAW, I, ON BEHALF OF MYSELF, MY FAMILY MEMBERS AND DEPENDENTS HEREBY RELEASE, DISCHARGE, AND COVENANT NOT TO SUE Randonneurs USA ("RUSA"), the San Francisco Randonneurs, the Event Organizer, their respective administrators, directors, agents, officers, members, volunteers, other riders, and owners and lessors of premises on which the Activity takes place, ("RELEASED PARTIES") FROM ALL LIABILITY, CLAIMS, DEMANDS, ACTIONS, LOSSES, COSTS OR DAMAGES (HEREAFTER, "CLAIMS") CAUSED OR ALLEGED TO BE CAUSED IN WHOLE OR IN PART BY THE ACTS OR OMISSIONS, INCLUDING NEGLIGENCE, OF THE "RELEASED PARTIES", INCLUDING, WITHOUT LIMITATION, RESCUE OPERATIONS. I further agree that if, I, or anyone on my behalf, makes a Claim against any of the Released Parties, I WILL INDEMNIFY, SAVE, AND HOLD HARMLESS EACH OF THE RELEASED PARTIES from any litigation expenses, attorney fees, losses, liability, damages, or costs which any Released Party may incur as the result of such Claim.

This agreement shall be construed broadly to provide a release and waiver to the maximum extent permissible under applicable law.

I AM 18 YEARS OF AGE OR OLDER, HAVE READ AND UNDERSTAND THE TERMS OF THIS AGREEMENT, UNDERSTAND THAT I AM GIVING UP SUBSTANTIAL RIGHTS BY SIGNING THIS AGREEMENT, HAVE SIGNED IT VOLUNTARILY AND WITHOUT ANY INDUCEMENT OR ASSURANCE OF ANY NATURE AND INTEND IT TO BE A COMPLETE AND UNCONDITIONAL RELEASE OF ALL LIABILITY, I INTEND THAT THIS AGREEMENT ALSO SHALL BE BINDING UPON MY HEIRS, NEXT OF KIN, REPRESENTATIVES, SUCCESSORS AND ASSIGNS. I AGREE THAT IF ANY PORTION OF THIS AGREEMENT IS HELD TO BE INVALID, THE BALANCE, NOTWITHSTANDING, SHALL CONTINUE IN FULL FORCE AND EFFECT.

I acknowledge and agree that the RELEASE AND WAIVER OF LIABILITY, ASSUMPTION OF RISK, AND INDEMNITY AGREEMENT may be executed and delivered by electronic means, and the electronic signature shall be considered an original signature for all purposes and shall have the same force and effect as an original signature. An electronic signature shall include an electronically scanned original signature or an electronically transmitted original signature (e.q. via pdf).$WAIVER$,
  effective_at  = NOW()
WHERE club_id IS NULL;

-- Fresh-DB fallback: insert if no global default row exists at all.
INSERT INTO rp_waiver_version (version_label, waiver_text, effective_at, club_id)
SELECT
  'SFR-2025-v2',
  $WAIVER2$I, IN CONSIDERATION of being permitted to participate in any way in the San Francisco Randonneurs' Designated Event, ("Activity"), I hereby acknowledge, agree, attest and represent the following:

1. I FULLY UNDERSTAND that: (a) bicycle riding is dangerous and represents an extreme test of a person's physical and mental limits. I understand that participation involves risks and dangers which include, without limitation, the potential for serious bodily injury, permanent disability, paralysis, illness and death, including exposure to viral infections such as COVID-19; loss of, or damage, to equipment/property; exposure to extreme conditions and circumstances; contact or collision with other bicycle riders, people, vehicles, animals, or other natural or manmade objects; imperfect course conditions; road and surface hazards; inadequate safety measures; other riders of varying skill levels; situations beyond the immediate control of anyone; and other undefined risks and dangers which may not be readily foreseeable or are presently unknown ("Risks"); (b) I understand that these Risks may be caused in whole or in part by my own actions or inactions, the actions or inactions of others, or the acts, inaction or negligence of the Released Parties defined below, and (c) there may be other risks and social and economic losses, costs and damages to me, my family members and dependents either not known to me or not readily foreseeable at this time; and I FULLY ACCEPT AND ASSUME ALL SUCH RISKS AND ALL RESPONSIBILITY FOR ALL LOSSES, COSTS, AND DAMAGES I, my family members and dependents may incur as a result of my participating and riding in the Activity.

2. I am qualified, in good health, and in proper physical condition to participate in the Activity. I agree and warrant that if, at any time, I believe conditions, including road hazards, to be unsafe or if I am not feeling well, I will immediately discontinue further riding of the Activity.

3. TO THE FULLEST EXTENT PERMITTED BY LAW, I, ON BEHALF OF MYSELF, MY FAMILY MEMBERS AND DEPENDENTS HEREBY RELEASE, DISCHARGE, AND COVENANT NOT TO SUE Randonneurs USA ("RUSA"), the San Francisco Randonneurs, the Event Organizer, their respective administrators, directors, agents, officers, members, volunteers, other riders, and owners and lessors of premises on which the Activity takes place, ("RELEASED PARTIES") FROM ALL LIABILITY, CLAIMS, DEMANDS, ACTIONS, LOSSES, COSTS OR DAMAGES (HEREAFTER, "CLAIMS") CAUSED OR ALLEGED TO BE CAUSED IN WHOLE OR IN PART BY THE ACTS OR OMISSIONS, INCLUDING NEGLIGENCE, OF THE "RELEASED PARTIES", INCLUDING, WITHOUT LIMITATION, RESCUE OPERATIONS. I further agree that if, I, or anyone on my behalf, makes a Claim against any of the Released Parties, I WILL INDEMNIFY, SAVE, AND HOLD HARMLESS EACH OF THE RELEASED PARTIES from any litigation expenses, attorney fees, losses, liability, damages, or costs which any Released Party may incur as the result of such Claim.

This agreement shall be construed broadly to provide a release and waiver to the maximum extent permissible under applicable law.

I AM 18 YEARS OF AGE OR OLDER, HAVE READ AND UNDERSTAND THE TERMS OF THIS AGREEMENT, UNDERSTAND THAT I AM GIVING UP SUBSTANTIAL RIGHTS BY SIGNING THIS AGREEMENT, HAVE SIGNED IT VOLUNTARILY AND WITHOUT ANY INDUCEMENT OR ASSURANCE OF ANY NATURE AND INTEND IT TO BE A COMPLETE AND UNCONDITIONAL RELEASE OF ALL LIABILITY, I INTEND THAT THIS AGREEMENT ALSO SHALL BE BINDING UPON MY HEIRS, NEXT OF KIN, REPRESENTATIVES, SUCCESSORS AND ASSIGNS. I AGREE THAT IF ANY PORTION OF THIS AGREEMENT IS HELD TO BE INVALID, THE BALANCE, NOTWITHSTANDING, SHALL CONTINUE IN FULL FORCE AND EFFECT.

I acknowledge and agree that the RELEASE AND WAIVER OF LIABILITY, ASSUMPTION OF RISK, AND INDEMNITY AGREEMENT may be executed and delivered by electronic means, and the electronic signature shall be considered an original signature for all purposes and shall have the same force and effect as an original signature. An electronic signature shall include an electronically scanned original signature or an electronically transmitted original signature (e.q. via pdf).$WAIVER2$,
  NOW(),
  NULL
WHERE NOT EXISTS (SELECT 1 FROM rp_waiver_version WHERE club_id IS NULL);
