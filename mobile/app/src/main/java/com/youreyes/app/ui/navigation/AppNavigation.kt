package com.youreyes.app.ui.navigation

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.viewmodel.compose.viewModel
import com.youreyes.app.voice.MicRationaleDialog
import com.youreyes.app.voice.VoiceFab
import com.youreyes.app.voice.VoicePanel
import com.youreyes.app.voice.VoiceUi
import com.youreyes.app.voice.VoiceViewModel
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Share
import androidx.compose.material.icons.filled.Star
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.navigation.NavGraphBuilder
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.NavType
import androidx.navigation.navArgument
import com.youreyes.app.care.CareRoute
import com.youreyes.app.core.FeatureFlags
import com.youreyes.app.messages.ChatRoute
import com.youreyes.app.ui.activitylog.ActivityLogRoute
import com.youreyes.app.ui.album.AlbumRoute
import com.youreyes.app.ui.auth.AuthRoute
import com.youreyes.app.ui.community.CommunityScreen
import com.youreyes.app.ui.display.DisplayRoute
import com.youreyes.app.ui.features.FeaturesScreen
import com.youreyes.app.ui.functiontest.FunctionTestRoute
import com.youreyes.app.ui.glasses.GlassesLinkRoute
import com.youreyes.app.ui.guide.UserGuideScreen
import com.youreyes.app.ui.meeting.MeetingRoute
import com.youreyes.app.ui.overview.OverviewRoute
import com.youreyes.app.ui.profile.ProfileRoute
import com.youreyes.app.ui.readiness.ReadinessRoute
import com.youreyes.app.ui.safety.SafetyScreen
import com.youreyes.app.ui.support.SupportRoute
import com.youreyes.app.ui.theme.AppTheme
import com.youreyes.app.ui.translation.TranslationRoute

/**
 * Navigation.
 *
 * This replaces a `var selectedTab: Int` with a `when (selectedTab)` over
 * thirteen branches. That structure had one consequence worth spelling out,
 * because it is the kind of bug that only bites the people this app is for: it
 * had no back stack, so the system Back button on any of the eight screens
 * reachable only from a card — Album, Nhật ký, Hỗ trợ, Tài khoản, and the rest —
 * closed the app instead of returning to where you came from. Someone navigating
 * by touch exploration cannot see that they have left; they simply find the app
 * gone.
 *
 * Five destinations sit in the bottom bar. Everything else is pushed onto the
 * stack, which means Back works, and it means each of those screens can show a
 * back affordance of its own rather than depending on a hardware gesture.
 */
object Routes {
    // Bottom bar
    const val HOME = "home"
    const val FEATURES = "features"
    const val SAFETY = "safety"
    const val COMMUNITY = "community"
    const val PROFILE = "profile"

    // Pushed
    const val GLASSES = "glasses"
    const val ACTIVITY_LOG = "activity-log"
    const val AUTH = "auth"
    const val SUPPORT = "support"
    const val ALBUM = "album"
    const val GUIDE = "guide"
    const val TRANSLATION = "translation"
    const val MEETING = "meeting"
    const val DISPLAY = "display"
    const val DEV_TEST = "dev-test"
    const val CARE = "care"
    const val READINESS = "readiness"
    const val CHAT = "chat/{peerUserId}?name={name}"

    /** Conversation with the glasses owner [peerUserId]; [name] only titles the screen. */
    fun chatRoute(peerUserId: String, name: String): String =
        "chat/${android.net.Uri.encode(peerUserId)}?name=${android.net.Uri.encode(name)}"
}

private data class BottomDestination(
    val route: String,
    val label: String,
    val icon: ImageVector,
)

private val allBottomDestinations = listOf(
    BottomDestination(Routes.HOME, "Trang chủ", Icons.Default.Home),
    BottomDestination(Routes.FEATURES, "Tính năng", Icons.Default.Star),
    BottomDestination(Routes.SAFETY, "An toàn", Icons.Default.Favorite),
    BottomDestination(Routes.COMMUNITY, "Cộng đồng", Icons.Default.Share),
    BottomDestination(Routes.PROFILE, "Hồ sơ", Icons.Default.Person),
)

/** The bottom bar without the destinations a [FeatureFlags] flag has switched off. */
private val bottomDestinations = allBottomDestinations.filter {
    it.route != Routes.COMMUNITY || FeatureFlags.COMMUNITY
}

/** Bottom-bar screens that also carry the floating "Nói lệnh" button (Home has its own big one). */
private val voiceFabRoutes = setOf(Routes.FEATURES, Routes.SAFETY, Routes.PROFILE, Routes.CARE)

@Composable
fun AppRoot(requestedRoute: String? = null, onRouteHandled: () -> Unit = {}) {
    val navController = rememberNavController()
    // A notification (login needed, new message) can ask for a screen; the nav graph may not be
    // ready on the very first frame, so wait for it.
    androidx.compose.runtime.LaunchedEffect(requestedRoute) {
        if (requestedRoute != null) {
            runCatching { navController.navigate(requestedRoute) }
            onRouteHandled()
        }
    }
    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination?.route

    // ---- voice control: one conversation shared by Home's big button and the floating button ----
    val context = LocalContext.current
    val view = LocalView.current
    val voiceViewModel: VoiceViewModel = viewModel()
    val voiceState by voiceViewModel.state.collectAsState()
    var showMicRationale by remember { mutableStateOf(false) }
    val micLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) {
            voiceViewModel.startListening()
        } else {
            val activity = context.findActivity()
            // After a refusal Android only says "explain again" the first time; later it never asks.
            val canAskAgain = activity != null &&
                ActivityCompat.shouldShowRequestPermissionRationale(activity, Manifest.permission.RECORD_AUDIO)
            voiceViewModel.onMicDenied(permanently = !canAskAgain)
        }
    }
    val onMic: () -> Unit = {
        val granted = ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        if (granted) voiceViewModel.onMicPressed() else showMicRationale = true
    }
    val voiceUi = VoiceUi(
        state = voiceState,
        onMic = onMic,
        onStop = { voiceViewModel.stop("Đã dừng.") },
        onDismiss = voiceViewModel::dismissMessage,
        onOpenSettings = {
            context.startActivity(
                Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${context.packageName}"))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            )
        },
    )

    androidx.compose.runtime.LaunchedEffect(voiceViewModel) {
        voiceViewModel.navigation.collect { route -> navController.openFromVoice(route) }
    }
    androidx.compose.runtime.LaunchedEffect(voiceViewModel, view) {
        voiceViewModel.announcements.collect { text -> if (text.isNotBlank()) view.announceForAccessibility(text) }
    }

    Scaffold(
        containerColor = Color.Transparent,
        bottomBar = {
            if (currentRoute in bottomDestinations.map { it.route }) {
                AppBottomBar(navController = navController, currentRoute = currentRoute)
            }
        },
    ) { padding ->
        val screenModifier = Modifier
            .fillMaxSize()
            .padding(padding)

        Box(modifier = Modifier.fillMaxSize()) {
            NavHost(
                navController = navController,
                startDestination = Routes.HOME,
                modifier = Modifier.fillMaxSize(),
            ) {
                appDestinations(navController, screenModifier, voiceUi)
            }

            // Floating voice controls, above the bottom bar. The chat keeps its own composer clear.
            if (currentRoute != null && currentRoute != Routes.CHAT) {
                Column(
                    modifier = Modifier
                        .align(Alignment.BottomEnd)
                        .padding(padding)
                        .padding(start = 16.dp, end = 16.dp, bottom = 12.dp),
                    horizontalAlignment = Alignment.End,
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    VoicePanel(ui = voiceUi)
                    if (currentRoute in voiceFabRoutes) VoiceFab(ui = voiceUi)
                }
            }
        }
    }

    if (showMicRationale) {
        MicRationaleDialog(
            onAllow = {
                showMicRationale = false
                micLauncher.launch(Manifest.permission.RECORD_AUDIO)
            },
            onLater = { showMicRationale = false },
        )
    }
}

/** Opens [route] for a voice command: bottom-bar screens like the bar does, "Trang chủ" by going back to it. */
private fun NavHostController.openFromVoice(route: String) {
    runCatching {
        when (route) {
            Routes.HOME -> popBackStack(Routes.HOME, inclusive = false)
            in bottomDestinations.map { it.route } -> navigate(route) {
                popUpTo(Routes.HOME) { saveState = true }
                launchSingleTop = true
                restoreState = true
            }
            else -> navigate(route) { launchSingleTop = true }
        }
    }
}

private tailrec fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.findActivity()
    else -> null
}

private fun NavGraphBuilder.appDestinations(
    navController: NavHostController,
    modifier: Modifier,
    voice: VoiceUi,
) {
    composable(Routes.HOME) {
        OverviewRoute(
            voice = voice,
            modifier = modifier,
            onNavigateToActivityLog = { navController.navigate(Routes.ACTIVITY_LOG) },
            onNavigateToGuide = { navController.navigate(Routes.GUIDE) },
            onNavigateToCare = { navController.navigate(Routes.CARE) },
            onNavigateToGlasses = { navController.navigate(Routes.GLASSES) },
            onNavigateToReadiness = { navController.navigate(Routes.READINESS) },
            onOpenChat = { peerUserId, name -> navController.navigate(Routes.chatRoute(peerUserId, name)) },
        )
    }
    composable(Routes.FEATURES) { FeaturesScreen(modifier = modifier) }
    composable(Routes.SAFETY) {
        SafetyScreen(
            modifier = modifier,
            onNavigateToProfile = { navController.navigate(Routes.PROFILE) },
        )
    }
    if (FeatureFlags.COMMUNITY) composable(Routes.COMMUNITY) { CommunityScreen(modifier = modifier) }
    composable(Routes.PROFILE) {
        ProfileRoute(
            modifier = modifier,
            onNavigateToDevTest = { navController.navigate(Routes.DEV_TEST) },
            onNavigateToAuth = { navController.navigate(Routes.AUTH) },
            onNavigateToSupport = { navController.navigate(Routes.SUPPORT) },
            onNavigateToDisplay = { navController.navigate(Routes.DISPLAY) },
            onNavigateToGlasses = { navController.navigate(Routes.GLASSES) },
        )
    }

    // Pushed screens. Each gets a visible back control as well as the system
    // one: the system gesture is invisible, and nothing on the screen tells a
    // blind user it is there.
    val back: () -> Unit = { navController.popBackStack() }

    composable(Routes.READINESS) {
        ReadinessRoute(
            modifier = modifier,
            onBack = back,
            onOpenEmergencyContact = { navController.navigate(Routes.SAFETY) },
        )
    }
    composable(Routes.GLASSES) { GlassesLinkRoute(modifier = modifier, onBack = back) }
    composable(Routes.ACTIVITY_LOG) { ActivityLogRoute(modifier = modifier, onBack = back) }
    composable(Routes.AUTH) { AuthRoute(modifier = modifier, onBack = back) }
    composable(Routes.SUPPORT) { SupportRoute(modifier = modifier, onBack = back) }
    if (FeatureFlags.ALBUM) composable(Routes.ALBUM) { AlbumRoute(modifier = modifier, onBack = back) }
    composable(Routes.GUIDE) { UserGuideScreen(modifier = modifier, onBack = back) }
    if (FeatureFlags.TRANSLATION) composable(Routes.TRANSLATION) { TranslationRoute(modifier = modifier, onBack = back) }
    if (FeatureFlags.MEETING) composable(Routes.MEETING) { MeetingRoute(modifier = modifier, onBack = back) }
    composable(Routes.DISPLAY) { DisplayRoute(modifier = modifier, onBack = back) }
    composable(Routes.DEV_TEST) { FunctionTestRoute(modifier = modifier, onBack = back) }
    composable(Routes.CARE) {
        CareRoute(
            modifier = modifier,
            onBack = back,
            onNavigateToAuth = { navController.navigate(Routes.AUTH) },
            onOpenChat = { peerUserId, name -> navController.navigate(Routes.chatRoute(peerUserId, name)) },
        )
    }
    composable(
        route = Routes.CHAT,
        arguments = listOf(
            navArgument("peerUserId") { type = NavType.StringType },
            navArgument("name") { type = NavType.StringType; defaultValue = "" },
        ),
    ) { entry ->
        ChatRoute(
            peerUserId = entry.arguments?.getString("peerUserId").orEmpty(),
            peerName = entry.arguments?.getString("name").orEmpty(),
            modifier = modifier,
            onBack = back,
        )
    }
}

/**
 * Bottom bar.
 *
 * Taller and larger than the Material default: 88dp with 30dp icons and a label
 * that is always shown. The labels were 11sp and the icons 20dp, which is the
 * densest control in the app sitting at the smallest size in the app.
 *
 * Selection is announced through [stateDescription] as well as the selected
 * flag, because "Trang chủ, đang mở" tells someone where they are and a
 * highlighted pill does not.
 */
@Composable
private fun AppBottomBar(navController: NavHostController, currentRoute: String?) {
    val colors = AppTheme.colors
    // The bar grows with the text rather than clipping it. At "To", "Trang chủ"
    // and "Cộng đồng" need two lines; a fixed-height bar would either cut them
    // off or force the labels back down to a size the setting exists to avoid.
    val scale = AppTheme.typeScale

    NavigationBar(
        containerColor = colors.surface,
        contentColor = colors.ink,
        tonalElevation = 8.dp,
        modifier = Modifier.defaultMinSize(minHeight = 88.dp * scale),
    ) {
        bottomDestinations.forEach { destination ->
            val isSelected = currentRoute == destination.route
            NavigationBarItem(
                selected = isSelected,
                onClick = {
                    if (!isSelected) {
                        navController.navigate(destination.route) {
                            popUpTo(Routes.HOME) { saveState = true }
                            launchSingleTop = true
                            restoreState = true
                        }
                    }
                },
                icon = {
                    Box(
                        modifier = Modifier
                            .size(48.dp)
                            .clip(RoundedCornerShape(16.dp))
                            .background(
                                if (isSelected) colors.accentSoft else Color.Transparent,
                            ),
                        contentAlignment = Alignment.Center,
                    ) {
                        Icon(
                            imageVector = destination.icon,
                            contentDescription = null,
                            tint = if (isSelected) colors.accentText else colors.inkSecondary,
                            modifier = Modifier.size(30.dp),
                        )
                    }
                },
                label = {
                    Text(
                        text = destination.label,
                        style = MaterialTheme.typography.labelMedium,
                        color = if (isSelected) colors.accentText else colors.inkSecondary,
                        textAlign = TextAlign.Center,
                        maxLines = 2,
                    )
                },
                alwaysShowLabel = true,
                colors = NavigationBarItemDefaults.colors(
                    indicatorColor = Color.Transparent,
                ),
                modifier = Modifier.semantics {
                    stateDescription = if (isSelected) "đang mở" else "chưa mở"
                },
            )
        }
    }
}
