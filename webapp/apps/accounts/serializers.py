from rest_framework import serializers


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=256, trim_whitespace=False,
                                     style={'input_type': 'password'})


class IdentitySerializer(serializers.Serializer):
    """Who the session belongs to - the same identity reviews are attributed to."""

    username = serializers.CharField()
    email = serializers.EmailField(allow_blank=True)
    reviewer = serializers.CharField()
