import {
  Card,
  CardContent,
  Typography,
  Box,
  Chip,
  Stack,
  Divider,
} from "@mui/material";
import LocationOnIcon from "@mui/icons-material/LocationOn";
import PersonIcon from "@mui/icons-material/Person";

export default function PatientPanel({ patient }) {
  if (!patient) {
    return (
      <Card sx={{ boxShadow: 1, borderRadius: 3, flex: 1 }}>
        <CardContent
          sx={{
            p: 3,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            minHeight: "100%",
          }}
        >
          <Typography variant="body2" sx={{ color: "text.secondary" }}>
            No patient selected
          </Typography>
        </CardContent>
      </Card>
    );
  }

  const isInside = !!patient.location;

  return (
    <Card sx={{ boxShadow: 1, borderRadius: 3, flex: 1, overflow: "auto" }}>
      <CardContent sx={{ p: 3 }}>
        <Box sx={{ mb: 3 }}>
          <Typography variant="h5" sx={{ fontWeight: 700, mb: 0.5 }}>
            {patient.name}
          </Typography>
          <Typography variant="body2" sx={{ color: "text.secondary" }}>
            MRN: {patient.mrn}
          </Typography>
        </Box>

        <Divider sx={{ my: 2 }} />

        <Box sx={{ mb: 3 }}>
          <Chip
            label={isInside ? "Inside" : "Outside"}
            color={isInside ? "success" : "default"}
            variant="outlined"
            sx={{ fontWeight: 600, height: 28 }}
          />
        </Box>

        <Stack spacing={2}>
          {isInside ? (
            <>
              <Box sx={{ display: "flex", alignItems: "flex-start", gap: 1.5 }}>
                <LocationOnIcon
                  sx={{
                    fontSize: "1.4rem",
                    color: "primary.main",
                    flexShrink: 0,
                    mt: 0.5,
                  }}
                />
                <Box sx={{ flex: 1 }}>
                  <Typography
                    variant="caption"
                    sx={{ color: "text.secondary", display: "block", mb: 0.5 }}
                  >
                    Floor
                  </Typography>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {patient.location.floor}
                  </Typography>
                </Box>
              </Box>

              <Box sx={{ display: "flex", alignItems: "flex-start", gap: 1.5 }}>
                <PersonIcon
                  sx={{
                    fontSize: "1.4rem",
                    color: "primary.main",
                    flexShrink: 0,
                    mt: 0.5,
                  }}
                />
                <Box sx={{ flex: 1 }}>
                  <Typography
                    variant="caption"
                    sx={{ color: "text.secondary", display: "block", mb: 0.5 }}
                  >
                    Camera
                  </Typography>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {patient.location.camera_id}
                  </Typography>
                </Box>
              </Box>
            </>
          ) : (
            <Typography variant="body2" sx={{ color: "text.secondary", py: 1 }}>
              Patient is not currently inside premises
            </Typography>
          )}
        </Stack>
      </CardContent>
    </Card>
  );
}
