"""Request shapes for /api/v1/library/. Validation of *meaning* stays in
the domain layer: scripts.media re-checks rights records and scripts.
ownermedia decides whether an asset may be selected at all. These
serializers only refuse an obviously malformed request first.
"""
from rest_framework import serializers

import scripts.ownermedia as ownermedia


class LibraryScanSerializer(serializers.Serializer):
    """POST /api/v1/library/scan/ - inspect a folder of the owner's originals.

    Read-only on the owner's files: scripts.media.scan hashes and decodes
    them and writes only to the catalog. Nothing is moved, renamed or
    transcoded, so a wrong path costs a refusal, never a file.
    """
    path = serializers.CharField(max_length=4096)


class RightsSerializer(serializers.Serializer):
    """The rights record a person supplies for one asset.

    Every field here is a claim being made by a human, which is why none of
    them has a default: "unknown licence" must look different from "no
    answer yet". scripts.media.rights_record() is what decides whether the
    result is enough for production use.
    """
    source = serializers.CharField(max_length=2000)
    license = serializers.CharField(max_length=2000)
    commercial_use = serializers.BooleanField()
    evidence = serializers.CharField(max_length=4000)
    creator = serializers.CharField(max_length=500, required=False, allow_blank=True,
                                    allow_null=True, default=None)
    attribution_required = serializers.BooleanField(required=False, default=False)
    attribution_text = serializers.CharField(max_length=2000, required=False,
                                             allow_blank=True, allow_null=True, default=None)


class LibraryAnnotationSerializer(serializers.Serializer):
    """PUT /api/v1/library/{id}/ - what this asset shows and where it is from.

    The description is what a person saw when they looked at it; nothing
    infers it from a filename. Rights are optional here so classifying the
    library need not wait on licensing research - but an asset without them
    stays unselectable.
    """
    description = serializers.CharField(max_length=4000)
    source = serializers.CharField(max_length=2000)
    tags = serializers.ListField(
        child=serializers.CharField(max_length=80), required=False, default=list)
    origin = serializers.ChoiceField(choices=("owner", "generated", "unknown"),
                                     required=False, default="owner")
    rights = RightsSerializer(required=False, allow_null=True, default=None)


class OwnerMediaSelectionSerializer(serializers.Serializer):
    """PUT /api/v1/projects/{id}/owner-media/ - role -> ordered asset ids.

    A role present with an empty list stops using owner media for it and
    hands that stage back to the generators. A role left out of the body is
    untouched, so the visuals panel and the audio panel can save
    independently.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for role in ownermedia.ROLES:
            self.fields[role] = serializers.ListField(
                child=serializers.CharField(min_length=64, max_length=64),
                required=False)
        self.fields["visuals_mode"] = serializers.ChoiceField(
            choices=ownermedia.VISUAL_MODES, required=False)

    def validate(self, attrs):
        if not any(role in attrs for role in (*ownermedia.ROLES, "visuals_mode")):
            raise serializers.ValidationError(
                f"name at least one role to set: {', '.join(ownermedia.ROLES)}")
        return attrs
